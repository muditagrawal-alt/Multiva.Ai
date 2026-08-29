// Suppresses the console window that would otherwise appear behind the app on
// Windows release builds. Debug builds keep it so panics stay visible.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    multiva_studio_lib::run();
}
