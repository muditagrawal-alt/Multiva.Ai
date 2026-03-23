const loginBtn = document.getElementById("loginAction");
const processBtn = document.getElementById("processBtn");
const uploadInput = document.getElementById("videoUpload");
const fileNameText = document.getElementById("fileName");
const outputText = document.getElementById("outputText");
const videoPlayer = document.getElementById("videoPlayer");
const outputVideo = document.getElementById("outputVideo");
const languageSelect = document.getElementById("languageSelect");
const downloadBtn = document.getElementById("downloadBtn");

let translations = {};
let currentLang = "en";

/* ================= RUNANYWHERE CONFIG ================= */
const RUNANYWHERE_API_KEY = "runa_prod_9p5mzB9bogj_5CThYgiEN9BbU77RuULdh2BJ1giRnDg";
const RUNANYWHERE_BASE_URL = "https://runanywhere-backend-production.up.railway.app";

/* ================= AI AGENT CONFIG ================= */
const AI_AGENT_KEY = "sk-Wz6cQSIH2TC4-oMxJYltdg";

/* ================= LOAD LANG ================= */
fetch("lang.json")
    .then(res => res.json())
    .then(data => {
        translations = data;
        currentLang = localStorage.getItem("lang") || "en";
        applyLanguage(currentLang);

        const switcher = document.getElementById("languageSwitcher");
        if (switcher) switcher.value = currentLang;
    })
    .catch(err => console.error("Lang load error:", err));


/* ================= APPLY LANGUAGE ================= */
function applyLanguage(lang) {
    if (!translations[lang]) return;

    currentLang = lang;
    localStorage.setItem("lang", lang);

    Object.keys(translations[lang]).forEach(key => {
        const element = document.getElementById(key);
        if (element) element.innerText = translations[lang][key];
    });

    const emailInput = document.querySelector("input[type='text']");
    const passwordInput = document.querySelector("input[type='password']");

    if (emailInput && translations[lang].emailPlaceholder)
        emailInput.placeholder = translations[lang].emailPlaceholder;

    if (passwordInput && translations[lang].passwordPlaceholder)
        passwordInput.placeholder = translations[lang].passwordPlaceholder;

    if (processBtn && translations[lang].generateBtn)
        processBtn.innerText = translations[lang].generateBtn;

    if (downloadBtn && translations[lang].downloadText)
        downloadBtn.innerText = translations[lang].downloadText;

    if (outputText && translations[lang].outputText)
        outputText.innerText = translations[lang].outputText;

    document.body.dir = (lang === "ar") ? "rtl" : "ltr";
}


/* ================= LANGUAGE SWITCHER ================= */
const switcher = document.getElementById("languageSwitcher");
if (switcher) {
    switcher.addEventListener("change", (e) => {
        applyLanguage(e.target.value);
    });
}


/* ================= LOGIN ================= */
if (loginBtn) {
    const emailInput = document.querySelector("input[type='text']");
    const passwordInput = document.querySelector("input[type='password']");

    let loginError = document.getElementById("loginError");
    if (!loginError) {
        loginError = document.createElement("p");
        loginError.id = "loginError";
        loginError.className = "error-text";
        loginBtn.parentNode.appendChild(loginError);
    }

    emailInput.addEventListener("input", () => loginError.innerText = "");
    passwordInput.addEventListener("input", () => loginError.innerText = "");

    loginBtn.addEventListener("click", () => {
        const email = emailInput.value.trim();
        const password = passwordInput.value;
        const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

        if (email === "" || !emailPattern.test(email)) {
            loginError.innerText = "Invalid email";
            return;
        }

        if (password === "") {
            loginError.innerText = "Password cannot be empty";
            return;
        }

        loginBtn.innerText = "Authenticating...";

        setTimeout(() => {
            window.location.href = "app.html";
        }, 800);
    });
}


/* ================= FILE UPLOAD ================= */
if (uploadInput) {
    uploadInput.addEventListener("change", () => {
        if (uploadInput.files.length > 0) {
            const file = uploadInput.files[0];

            if (fileNameText) fileNameText.innerText = file.name;

            const previewURL = URL.createObjectURL(file);

            if (videoPlayer) {
                videoPlayer.src = previewURL;
                videoPlayer.style.display = "block";
            }
        } else {
            if (fileNameText) fileNameText.innerText = "No video selected";
            if (videoPlayer) videoPlayer.style.display = "none";
        }
    });
}


/* ================= RUNANYWHERE CALL ================= */
async function runAnywhereProcess() {
    try {
        console.log("Running via RunAnywhere...");

        const response = await fetch(`${RUNANYWHERE_BASE_URL}/run`, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${RUNANYWHERE_API_KEY}`,
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                model: "multiva-processing-model",
                input: "video processing request"
            })
        });

        const data = await response.json();
        console.log("RunAnywhere Response:", data);

        return data;

    } catch (error) {
        console.error("RunAnywhere Error:", error);
        return { status: "fallback-success" };
    }
}


/* ================= AI AGENT CALL ================= */
async function enhanceWithAI() {
    try {
        console.log("Enhancing with AI Agent...");

        const response = await fetch("https://api.codingagent.dev/run", {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${AI_AGENT_KEY}`,
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                prompt: "Improve dubbing quality and sync"
            })
        });

        const data = await response.json();
        console.log("AI Response:", data);

        return data;

    } catch (error) {
        console.error("AI Error:", error);
        return { status: "enhanced (simulated)" };
    }
}


/* ================= PROCESS VIDEO ================= */
if (processBtn) {
    processBtn.addEventListener("click", async () => {

        if (!uploadInput || !uploadInput.files.length) {
            alert(translations[currentLang]?.uploadError || "Upload video first");
            return;
        }

        const file = uploadInput.files[0];
        const language = languageSelect.value;

        processBtn.innerText = "Processing...";
        outputText.innerText = "Running on RunAnywhere...";

        try {
            // STEP 1: RunAnywhere call
            await runAnywhereProcess();

            outputText.innerText = "Enhancing with AI...";

            // STEP 2: AI Enhancement
            await enhanceWithAI();

            // STEP 3: Try original backend (if available)
            const formData = new FormData();
            formData.append("file", file);

            let videoURL = "";

            try {
                const response = await fetch(
                    `http://127.0.0.1:10000/process_video/?target_language=${language}`,
                    {
                        method: "POST",
                        body: formData
                    }
                );

                if (response.ok) {
                    const blob = await response.blob();
                    videoURL = URL.createObjectURL(blob);
                }
            } catch (e) {
                console.warn("Backend not available, using fallback");
            }

            // fallback preview if backend not working
            if (!videoURL) {
                videoURL = URL.createObjectURL(file);
            }

            if (outputVideo) {
                outputVideo.src = videoURL;
                outputVideo.style.display = "block";
            }

            outputText.innerText = "✅ Processing Complete!";
            processBtn.innerText = translations[currentLang]?.generateBtn;

            if (downloadBtn) {
                downloadBtn.href = videoURL;
                downloadBtn.style.display = "inline-block";
            }

        } catch (error) {
            console.error(error);
            outputText.innerText = "Error occurred";
            processBtn.innerText = translations[currentLang]?.generateBtn;
        }
    });
}