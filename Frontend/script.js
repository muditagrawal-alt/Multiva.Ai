// ===== ELEMENTS =====
const loginBtn = document.getElementById("loginAction");
const processBtn = document.getElementById("processBtn");
const uploadInput = document.getElementById("videoUpload");
const fileNameText = document.getElementById("noVideoText");
const outputText = document.getElementById("outputText");
const videoPlayer = document.getElementById("videoPlayer");
const outputVideo = document.getElementById("outputVideo");
const languageSelect = document.getElementById("languageSelect");
const downloadBtn = document.getElementById("downloadBtn");
const switcher = document.getElementById("languageSwitcher");

let translations = {};
let currentLang = "en";


// ===== LOAD LANGUAGE =====
window.addEventListener("DOMContentLoaded", () => {
    fetch("lang.json")
        .then(res => res.json())
        .then(data => {
            translations = data;

            currentLang = localStorage.getItem("lang") || "en";

            applyLanguage(currentLang);

            if (switcher) switcher.value = currentLang;
        })
        .catch(err => console.error("Lang load error:", err));
});


// ===== APPLY LANGUAGE =====
function applyLanguage(lang) {

    if (!translations[lang]) return;

    currentLang = lang;
    localStorage.setItem("lang", lang);

    const t = translations[lang];

    // 🔥 Update all matching IDs
    Object.keys(t).forEach(key => {
        const el = document.getElementById(key);
        if (el) el.innerText = t[key];
    });

    // 🔥 CRITICAL FIX: always set default text when no file
    if (fileNameText) {
        if (!uploadInput || !uploadInput.files.length) {
            fileNameText.innerText = t.noVideoText;
        }
    }

    // 🔥 Placeholders
    const emailInput = document.querySelector("input[type='text']");
    const passwordInput = document.querySelector("input[type='password']");

    if (emailInput) emailInput.placeholder = t.emailPlaceholder;
    if (passwordInput) passwordInput.placeholder = t.passwordPlaceholder;

    // 🔥 Buttons
    if (processBtn) processBtn.innerText = t.generateBtn;
    if (downloadBtn) downloadBtn.innerText = t.downloadText;
    if (outputText) outputText.innerText = t.outputText;

    // RTL
    document.body.dir = (lang === "ar") ? "rtl" : "ltr";
}


// ===== LANGUAGE SWITCHER =====
if (switcher) {
    switcher.addEventListener("change", (e) => {
        applyLanguage(e.target.value);
    });
}


// ===== LOGIN =====
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

    emailInput?.addEventListener("input", () => loginError.innerText = "");
    passwordInput?.addEventListener("input", () => loginError.innerText = "");

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


// ===== BACKEND LANGUAGE SELECT =====
if (languageSelect) {
    languageSelect.addEventListener("change", () => {
        console.log("Selected backend language:", languageSelect.value);
    });
}


// ===== FILE UPLOAD =====
if (uploadInput) {
    uploadInput.addEventListener("change", () => {

        if (uploadInput.files.length > 0) {
            const file = uploadInput.files[0];

            // 🔥 Show file name (no translation here)
            if (fileNameText) fileNameText.innerText = file.name;

            const previewURL = URL.createObjectURL(file);

            if (videoPlayer) {
                videoPlayer.src = previewURL;
                videoPlayer.style.display = "block";
            }

        } else {

            // 🔥 Restore translated "No video selected"
            if (fileNameText) {
                fileNameText.innerText = translations[currentLang]?.noVideoText || "No video selected";
            }

            if (videoPlayer) {
                videoPlayer.style.display = "none";
                videoPlayer.src = "";
            }
        }
    });
}


// ===== PROCESS VIDEO =====
if (processBtn) {
    processBtn.addEventListener("click", async () => {

        if (!uploadInput || !uploadInput.files.length) {
            alert("Upload video first");
            return;
        }

        const file = uploadInput.files[0];
        const language = languageSelect.value;

        processBtn.innerText = "Processing...";
        if (outputText) outputText.innerText = "Processing...";

        const formData = new FormData();
        formData.append("file", file);

        try {
            const response = await fetch(
                `http://127.0.0.1:10000/process_video/?target_language=${language}`,
                {
                    method: "POST",
                    body: formData
                }
            );

            if (!response.ok) throw new Error("Backend failed");

            const blob = await response.blob();
            const videoURL = URL.createObjectURL(blob);

            if (outputVideo) {
                outputVideo.src = videoURL;
                outputVideo.style.display = "block";
            }

            if (outputText) outputText.innerText = "Video Generated!";
            processBtn.innerText = translations[currentLang]?.generateBtn;

            if (downloadBtn) {
                downloadBtn.href = videoURL;
                downloadBtn.style.display = "inline-block";
            }

        } catch (error) {
            console.error(error);
            if (outputText) outputText.innerText = "Error occurred";
            processBtn.innerText = translations[currentLang]?.generateBtn;
        }
    });
}