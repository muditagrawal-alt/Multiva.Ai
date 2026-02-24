const loginBtn = document.getElementById("loginAction");
const processBtn = document.getElementById("processBtn");
const uploadInput = document.getElementById("videoUpload");
const fileNameText = document.getElementById("fileName");
const outputText = document.getElementById("outputText");

/* ✅ VIDEO PLAYER */
const videoPlayer = document.getElementById("videoPlayer");

/* LOGIN */
/* LOGIN VALIDATION */
if (loginBtn) {
    const emailInput = document.querySelector("input[type='text']");
    const passwordInput = document.querySelector("input[type='password']");

    // Create or select error message element
    let loginError = document.getElementById("loginError");
    if (!loginError) {
        loginError = document.createElement("p");
        loginError.id = "loginError";
        loginError.className = "error-text";
        loginBtn.parentNode.appendChild(loginError);
    }

    // Clear error on input
    emailInput.addEventListener("input", () => {
        loginError.innerText = "";
    });
    passwordInput.addEventListener("input", () => {
        loginError.innerText = "";
    });

    loginBtn.addEventListener("click", () => {
        const email = emailInput.value.trim();
        const password = passwordInput.value;

        // Email regex
        const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

        // Validation
        if (email === "" || !emailPattern.test(email)) {
            loginError.innerText = "Invalid email";
            return;
        }

        if (password === "") {
            loginError.innerText = "Password cannot be empty";
            return;
        }

        // If both valid, proceed
        loginError.innerText = "";
        loginBtn.innerText = "Authenticating...";

        setTimeout(() => {
            window.location.href = "app.html";
        }, 800);
    });
}

/* NAVIGATION */
function goToLogin() {
    window.location.href = "Login.html";
}

/* FILE UPLOAD + VIDEO PREVIEW */
if (uploadInput) {
    uploadInput.addEventListener("change", () => {

        if (uploadInput.files.length > 0) {

            const file = uploadInput.files[0];

            /* ✅ Update File Name */
            if (fileNameText) {
                fileNameText.innerText = file.name;
            }

            /* ✅ Video Preview */
            if (videoPlayer) {
                const videoURL = URL.createObjectURL(file);

                videoPlayer.src = videoURL;
                videoPlayer.style.display = "block";
            }

        } else {

            if (fileNameText) {
                fileNameText.innerText = "No video selected";
            }

            if (videoPlayer) {
                videoPlayer.style.display = "none";
            }
        }
    });
}

/* PROCESSING */
if (processBtn) {
    processBtn.addEventListener("click", () => {

        if (!uploadInput || !uploadInput.files.length) {
            alert("Please upload a video first!");
            return;
        }

        processBtn.innerText = "Processing AI Model...";

        if (outputText) {
            outputText.innerText = "Analyzing voice...";
        }

        setTimeout(() => {
            if (outputText) outputText.innerText = "Cloning voice...";
        }, 800);

        setTimeout(() => {
            if (outputText) outputText.innerText = "Generating translated video...";
        }, 1600);

        setTimeout(() => {
            processBtn.innerText = "Generate Output";
            if (outputText) {
                outputText.innerText = "✅ Video Successfully Generated (Demo)";
            }
        }, 2400);
    });
}
// const outputVideo = document.getElementById("outputVideo");

// processBtn.addEventListener("click", () => {
//     // simulate processing...
//     setTimeout(() => {
//         const videoURL = URL.createObjectURL(uploadInput.files[0]); // example
//         outputVideo.src = videoURL;
//         outputVideo.style.display = "block"; // show output video
//         outputText.innerText = "✅ Video Successfully Generated!";
//     }, 2400);
// });
const outputVideo = document.getElementById("outputVideo");
const downloadBtn = document.getElementById("downloadBtn");

processBtn.addEventListener("click", () => {

    if (!uploadInput.files.length) {
        alert("Please upload a video first!");
        return;
    }

    processBtn.innerText = "Processing AI Model...";
    outputText.innerText = "Analyzing video...";

    setTimeout(() => {
        outputText.innerText = "Cloning video...";
    }, 800);

    setTimeout(() => {
        outputText.innerText = "Generating output video...";
    }, 1600);

    setTimeout(() => {
        // Use uploaded file as demo output
        const file = uploadInput.files[0];
        const videoURL = URL.createObjectURL(file);

        outputVideo.src = videoURL;
        outputVideo.style.display = "block";

        // Setup download
        downloadBtn.href = videoURL;
        downloadBtn.style.display = "inline-block";

        outputText.innerText = "✅ Video Successfully Generated!";
        processBtn.innerText = "Generate Output";
    }, 2400);

});