// =============================================================================
// Multiva Studio - desktop shell
//
// The pipeline is a Python service. This shell finds the project checkout,
// starts the API, waits for it to answer, and points the webview at it. The
// user never sees a terminal.
//
// The webview loads http://127.0.0.1:<port>/app/ rather than the bundled files
// so the UI and the API share an origin, exactly as they do in a browser.
// Loading the bundled copy instead would make every fetch cross-origin and
// re-introduce the mixed-content and private-network problems that rule out
// hosting the studio remotely.
// =============================================================================

use std::{
    io::{Read, Write},
    net::TcpStream,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
};

use tauri::{Emitter, LogicalSize, Manager};

const DEFAULT_PORT: u16 = 8000;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(180);

/// Holds the API child process so it can be killed when the window closes.
#[derive(Default)]
struct Server(Mutex<Option<Child>>);

impl Drop for Server {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.0.lock() {
            if let Some(child) = guard.as_mut() {
                let _ = child.kill();
            }
        }
    }
}

/// Python lives in a different place on Windows than everywhere else.
fn venv_python(root: &Path) -> Option<PathBuf> {
    let candidates = if cfg!(windows) {
        vec![root.join("venv/Scripts/python.exe"), root.join(".venv/Scripts/python.exe")]
    } else {
        vec![root.join("venv/bin/python"), root.join(".venv/bin/python")]
    };
    candidates.into_iter().find(|p| p.is_file())
}

/// A checkout is identified by the file the API actually lives in, not by name,
/// so a renamed or relocated folder still works.
fn looks_like_checkout(dir: &Path) -> bool {
    dir.join("Backend_pipeline/app.py").is_file()
}

/// Search order: explicit override, then upward from the executable, then
/// upward from the working directory. Covers both `tauri dev` and a bundled
/// app sitting inside the checkout.
fn find_project_root() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("MULTIVA_ROOT") {
        let path = PathBuf::from(explicit);
        if looks_like_checkout(&path) {
            return Some(path);
        }
    }

    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        starts.push(exe);
    }
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }

    for start in starts {
        let mut dir = start.as_path();
        // Bounded so a misconfigured install cannot scan the whole disk, but
        // deep enough to climb out of a macOS bundle: the executable sits at
        // target/release/bundle/macos/Multiva Studio.app/Contents/MacOS/, which
        // is ten hops below the checkout. At eight the bundled app never found
        // the project and silently started no engine.
        for _ in 0..16 {
            if looks_like_checkout(dir) {
                return Some(dir.to_path_buf());
            }
            match dir.parent() {
                Some(parent) => dir = parent,
                None => break,
            }
        }
    }
    None
}

fn port_is_open(port: u16) -> bool {
    TcpStream::connect_timeout(
        &([127, 0, 0, 1], port).into(),
        Duration::from_millis(400),
    )
    .is_ok()
}

fn spawn_api(root: &Path, python: &Path, port: u16) -> std::io::Result<Child> {
    let mut cmd = Command::new(python);
    cmd.current_dir(root.join("Backend_pipeline"))
        .args(["-m", "uvicorn", "app:app", "--port", &port.to_string()])
        // MPS has gaps in kernel coverage; let torch fall back rather than crash.
        .env("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        .env("TOKENIZERS_PARALLELISM", "false")
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    // Without this a console window flashes up on every launch on Windows.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    cmd.spawn()
}

/// Reported to the splash screen so a failure explains itself.
#[derive(Clone, serde::Serialize)]
struct Status {
    stage: String,
    detail: String,
    progress: u8,
    fatal: bool,
}

fn emit(app: &tauri::AppHandle, stage: &str, detail: &str, progress: u8, fatal: bool) {
    let _ = app.emit(
        "startup",
        Status { stage: stage.into(), detail: detail.into(), progress, fatal },
    );
}

/// One-shot HTTP GET against the local engine.
///
/// Hand-rolled rather than pulling in an HTTP client: this asks localhost for
/// one small JSON document and nothing about that needs connection pooling,
/// TLS or redirects.
fn http_get(port: u16, path: &str) -> Option<String> {
    let mut stream = TcpStream::connect_timeout(
        &([127, 0, 0, 1], port).into(),
        Duration::from_millis(600),
    )
    .ok()?;
    stream.set_read_timeout(Some(Duration::from_millis(2000))).ok()?;
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut raw = String::new();
    stream.read_to_string(&mut raw).ok()?;
    // Headers and body are separated by a blank line; everything after it is
    // the JSON we asked for.
    raw.split("\r\n\r\n").nth(1).map(str::to_owned)
}

/// The engine's warm-up state: which model it is loading, and whether it is done.
struct Boot {
    stage: String,
    progress: u8,
    ready: bool,
    /// Components that failed to load. The engine still starts without them;
    /// the stage that needs one will fail with a real message.
    notes: Vec<String>,
}

fn read_boot(port: u16) -> Option<Boot> {
    let body = http_get(port, "/api/boot")?;
    let v: serde_json::Value = serde_json::from_str(&body).ok()?;
    let total = v["total"].as_u64().unwrap_or(1).max(1);
    let index = v["index"].as_u64().unwrap_or(0);
    Some(Boot {
        stage: v["stage"].as_str().unwrap_or("Loading").to_string(),
        progress: ((index as f64 / total as f64) * 100.0).round().min(100.0) as u8,
        ready: v["ready"].as_bool().unwrap_or(false),
        notes: v["notes"]
            .as_array()
            .map(|a| a.iter().filter_map(|n| n.as_str().map(str::to_owned)).collect())
            .unwrap_or_default(),
    })
}

fn boot(app: tauri::AppHandle) {
    let port: u16 = std::env::var("MULTIVA_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_PORT);

    // Reuse a server the user already started from a terminal instead of
    // fighting it for the port.
    if port_is_open(port) {
        emit(&app, "ready", "Attached to a server already running.", 100, false);
        show_studio(&app, port);
        return;
    }

    let root = match find_project_root() {
        Some(r) => r,
        None => {
            emit(&app, "error",
                 "Could not find the Multiva project. Put this app inside the \
                  checkout, or set MULTIVA_ROOT to its path.", 0, true);
            return;
        }
    };

    let python = match venv_python(&root) {
        Some(p) => p,
        None => {
            emit(&app, "error",
                 "No Python environment found. Run the install step first:\n\
                  python3 -m venv venv && ./venv/bin/pip install -r requirements.txt", 0, true);
            return;
        }
    };

    emit(&app, "starting", "Starting the local engine", 0, false);

    let child = match spawn_api(&root, &python, port) {
        Ok(c) => c,
        Err(e) => {
            emit(&app, "error", &format!("Could not start the engine: {e}"), 0, true);
            return;
        }
    };
    if let Some(state) = app.try_state::<Server>() {
        *state.0.lock().unwrap() = Some(child);
    }

    

    let began = Instant::now();
    while began.elapsed() < STARTUP_TIMEOUT {
        if let Some(boot) = read_boot(port) {
            emit(&app, "loading", &boot.stage, boot.progress, false);
            if boot.ready {
                // Reporting "Ready" while a model failed to load would push the
                // discovery of that failure into the middle of someone's render.
                if !boot.notes.is_empty() {
                    emit(&app, "warning", &boot.notes.join(". "), 100, false);
                    std::thread::sleep(Duration::from_millis(2600));
                }
                show_studio(&app, port);
                return;
            }
        }
        std::thread::sleep(Duration::from_millis(700));
    }

    emit(&app, "error",
         "The engine did not finish loading in time. Run it manually to see why:\n\
          cd Backend_pipeline && ../venv/bin/python -m uvicorn app:app --port 8000", 0, true);
}

fn show_studio(app: &tauri::AppHandle, port: u16) {
    if let Some(window) = app.get_webview_window("main") {
        // The splash is sized for a splash. The workspace needs room, and the
        // floor rises with it so the panels cannot be crushed afterwards.
        let _ = window.set_min_size(Some(LogicalSize::new(880.0, 620.0)));
        let _ = window.set_size(LogicalSize::new(1180.0, 840.0));
        let _ = window.center();

        let url = format!("http://127.0.0.1:{port}/app/");
        if let Ok(parsed) = url.parse() {
            let _ = window.navigate(parsed);
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_log::Builder::new().build())
        .manage(Server::default())
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_min_size(Some(LogicalSize::new(720.0, 400.0)));
            }
            let handle = app.handle().clone();
            // Booting blocks on network polling, so keep it off the UI thread.
            std::thread::spawn(move || boot(handle));
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<Server>() {
                    if let Some(child) = state.0.lock().unwrap().as_mut() {
                        let _ = child.kill();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Multiva Studio");
}
