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

    // Update all text elements
    Object.keys(translations[lang]).forEach(key => {
        const element = document.getElementById(key);
        if (element) {
            element.innerText = translations[lang][key];
        }
    });

    // 🔥 Update placeholders (LOGIN PAGE)
    const emailInput = document.querySelector("input[type='text']");
    const passwordInput = document.querySelector("input[type='password']");

    if (emailInput && translations[lang].emailPlaceholder) {
        emailInput.placeholder = translations[lang].emailPlaceholder;
    }

    if (passwordInput && translations[lang].passwordPlaceholder) {
        passwordInput.placeholder = translations[lang].passwordPlaceholder;
    }

    // 🔥 Buttons / dynamic text
    if (processBtn && translations[lang].generateBtn) {
        processBtn.innerText = translations[lang].generateBtn;
    }

    if (downloadBtn && translations[lang].downloadText) {
        downloadBtn.innerText = translations[lang].downloadText;
    }

    if (outputText && translations[lang].outputText) {
        outputText.innerText = translations[lang].outputText;
    }

    // RTL support
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


/* ================= LANGUAGE SELECT (BACKEND ONLY) ================= */
if (languageSelect) {
    languageSelect.addEventListener("change", () => {
        console.log("Selected backend language:", languageSelect.value);
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
        outputText.innerText = translations[currentLang]?.processingText || "Processing...";

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

            outputText.innerText = translations[currentLang]?.successMsg || "Video Generated!";
            processBtn.innerText = translations[currentLang]?.generateBtn;

            if (downloadBtn) {
                downloadBtn.href = videoURL;
                downloadBtn.style.display = "inline-block";
            }

        } catch (error) {
            console.error(error);
            outputText.innerText = translations[currentLang]?.errorMsg || "Error occurred";
            processBtn.innerText = translations[currentLang]?.generateBtn;
        }
    });
}