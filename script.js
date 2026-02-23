const loginBtn = document.getElementById("loginAction");
const processBtn = document.getElementById("processBtn");
const uploadInput = document.getElementById("videoUpload");
const fileNameText = document.getElementById("fileName");
const outputText = document.getElementById("outputText");

/* LOGIN */
if (loginBtn) {
    loginBtn.addEventListener("click", () => {
        loginBtn.innerText = "Authenticating...";

        setTimeout(() => {
            window.location.href = "app.html";
        }, 800);
    });
}

function goToLogin() {
    window.location.href = "login.html";
}
/* FILE UPLOAD */
if (uploadInput) {
    uploadInput.addEventListener("change", () => {
        if (uploadInput.files.length > 0) {
            fileNameText.innerText = uploadInput.files[0].name;
        } else {
            fileNameText.innerText = "No video selected";
        }
    });
}

/* PROCESSING */
if (processBtn) {
    processBtn.addEventListener("click", () => {

        if (!uploadInput.files.length) {
            alert("Please upload a video first!");
            return;
        }

        processBtn.innerText = "Processing AI Model...";

        outputText.innerText = "Analyzing voice...";
        
        setTimeout(() => {
            outputText.innerText = "Cloning voice...";
        }, 800);

        setTimeout(() => {
            outputText.innerText = "Generating translated video...";
        }, 1600);

        setTimeout(() => {
            processBtn.innerText = "Generate Output";
            outputText.innerText = "✅ Video Successfully Generated (Demo)";
        }, 2400);
    });
}