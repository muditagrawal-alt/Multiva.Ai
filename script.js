const loginBtn = document.getElementById("loginAction");
const processBtn = document.getElementById("processBtn");
const uploadInput = document.getElementById("videoUpload");
const fileNameText = document.getElementById("fileName");
const outputText = document.getElementById("outputText");
const videoPlayer = document.getElementById("videoPlayer");
const outputVideo = document.getElementById("outputVideo");
const languageSelect = document.getElementById("languageSelect");
const downloadBtn = document.getElementById("downloadBtn");

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

        loginError.innerText = "";
        loginBtn.innerText = "Authenticating...";

        setTimeout(() => {
            window.location.href = "app.html";
        }, 800);
    });
}

/* ================= LANGUAGE SELECTOR ================= */
if (languageSelect) {
    languageSelect.addEventListener("change", () => {
        const selectedText =
            languageSelect.options[languageSelect.selectedIndex].text;
        outputText.innerText = `Selected Language: ${selectedText}`;
    });
}

/* ================= FILE UPLOAD PREVIEW ================= */
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

/* ================= PROCESS VIDEO (REAL BACKEND CALL) ================= */
if (processBtn) {
    processBtn.addEventListener("click", async () => {

        if (!uploadInput || !uploadInput.files.length) {
            alert("Please upload a video first!");
            return;
        }

        const file = uploadInput.files[0];
        const language = languageSelect.value;

        processBtn.innerText = "Processing...";
        outputText.innerText = "Uploading and processing...";

        const formData = new FormData();
        formData.append("file", file);

        try {
            const response = await fetch(
                `http://localhost:10000/process_video/?target_language=${language}`,
                {
                    method: "POST",
                    body: formData
                }
            );

            if (!response.ok) {
                throw new Error("Backend processing failed");
            }

            const blob = await response.blob();
            const videoURL = URL.createObjectURL(blob);

            // Show output video
            if (outputVideo) {
                outputVideo.src = videoURL;
                outputVideo.style.display = "block";
            }

            outputText.innerText = "✅ Video Successfully Generated!";
            processBtn.innerText = "Generate Output";

            // ✅ FIXED DOWNLOAD BUTTON
            if (downloadBtn) {
                downloadBtn.href = videoURL;
                downloadBtn.style.display = "inline-block";
            }

        } catch (error) {
            console.error(error);
            outputText.innerText = "❌ Error processing video";
            processBtn.innerText = "Generate Output";
        }
    });
}