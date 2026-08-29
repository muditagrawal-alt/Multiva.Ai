const I18N_STORAGE_KEY = "multiva-language";
const RTL_LANGS = new Set(["ar"]);
const KNOWN_LANGUAGE_CODES = new Set(["en", "hi", "es", "fr", "ja", "de", "ar", "tr", "ru", "pt", "it", "ko", "nl", "cs", "pl"]);
const API_BASE_URL = (window.MULTIVA_API_BASE_URL || "http://127.0.0.1:10000").replace(/\/$/, "");
const LOGIN_REDIRECT_URL = "workspace.html";
const WORKSPACE_STAGE_KEYS = ["uploading", "cloning", "translating", "rendering"];

const PAGE_APPLIERS = {
  index: applyIndexPage,
  login: applyLoginPage,
  profile: applyProfilePage,
  workspace: applyWorkspacePage
};

let translationCache = null;
let currentLanguage = "en";
let loginInitialized = false;
let workspaceInitialized = false;

const workspaceState = {
  sourceFile: null,
  sourcePreviewUrl: null,
  outputPreviewUrl: null,
  outputBlob: null,
  outputFilename: "",
  stageIndex: 0,
  phase: "idle",
  stageTimer: null,
  toastTimer: null,
  elements: null
};

document.addEventListener("DOMContentLoaded", async () => {
  translationCache = await loadTranslations();
  currentLanguage = getSavedLanguage(translationCache);
  bindLanguageDropdown(translationCache);
  applyLanguage(currentLanguage, translationCache);
  initializeCurrentPage();
});

async function loadTranslations() {
  try {
    const response = await fetch("lang.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Unable to load translations: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error(error);
    return { defaultLanguage: "en", languages: { en: fallbackEnglish() } };
  }
}

function getSavedLanguage(dictionary) {
  const saved = localStorage.getItem(I18N_STORAGE_KEY);
  if (saved && KNOWN_LANGUAGE_CODES.has(saved)) {
    return saved;
  }
  return dictionary.defaultLanguage || "en";
}

function getLanguagePack(lang, dictionary) {
  return dictionary.languages[lang] || dictionary.languages[dictionary.defaultLanguage] || dictionary.languages.en;
}

function applyLanguage(lang, dictionary) {
  currentLanguage = lang;
  localStorage.setItem(I18N_STORAGE_KEY, lang);

  const pack = getLanguagePack(lang, dictionary);
  const page = document.body.dataset.page;

  document.documentElement.lang = lang;
  document.documentElement.dir = RTL_LANGS.has(lang) ? "rtl" : "ltr";
  syncDropdownState(lang, pack);

  const applyPage = PAGE_APPLIERS[page];
  if (applyPage) {
    applyPage(pack);
  }

  if (page === "profile" && typeof window.renderTable === "function") {
    patchProfileDynamicContent(pack);
  }
}

function bindLanguageDropdown(dictionary) {
  const btn = document.getElementById("lang-dropdown-btn");
  const panel = document.getElementById("lang-dropdown-panel");
  const chevron = document.getElementById("lang-chevron");
  const options = document.querySelectorAll(".lang-option");

  if (!btn || !panel || !chevron || !options.length) {
    return;
  }

  const openDropdown = () => {
    panel.classList.remove("opacity-0", "scale-95", "pointer-events-none");
    panel.classList.add("opacity-100", "scale-100");
    chevron.style.transform = "rotate(180deg)";
    btn.setAttribute("aria-expanded", "true");
  };

  const closeDropdown = () => {
    panel.classList.add("opacity-0", "scale-95", "pointer-events-none");
    panel.classList.remove("opacity-100", "scale-100");
    chevron.style.transform = "rotate(0deg)";
    btn.setAttribute("aria-expanded", "false");
  };

  btn.addEventListener("click", (event) => {
    event.stopPropagation();
    const isOpen = btn.getAttribute("aria-expanded") === "true";
    if (isOpen) {
      closeDropdown();
    } else {
      openDropdown();
    }
  });

  options.forEach((option) => {
    option.addEventListener("click", () => {
      const code = option.dataset.langCode || "en";
      applyLanguage(code, dictionary);
      closeDropdown();
    });
  });

  document.addEventListener("click", closeDropdown);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDropdown();
    }
  });
}

function syncDropdownState(lang, pack) {
  const label = document.getElementById("lang-selected-label");
  const options = document.querySelectorAll(".lang-option");
  const selectedOption = Array.from(options).find((option) => option.dataset.langCode === lang);

  if (label) {
    label.textContent = selectedOption?.dataset.lang || pack.languageLabel;
  }

  options.forEach((option) => {
    const code = option.dataset.langCode;
    const isSelected = code === lang;
    const check = document.getElementById(`check-${option.dataset.lang}`);
    option.setAttribute("aria-selected", String(isSelected));
    option.classList.toggle("text-on-surface", isSelected);
    option.classList.toggle("text-slate-400", !isSelected);
    if (check) {
      check.classList.toggle("hidden", !isSelected);
    }
  });
}

function applyIndexPage(pack) {
  const page = pack.index;
  document.title = page.metaTitle;

  const navLinks = document.querySelectorAll("nav .md\\:flex a");
  setNodeText(navLinks[0], page.nav.home);
  setNodeText(navLinks[1], page.nav.mission);
  setNodeText(navLinks[2], page.nav.features);
  setNodeText(navLinks[3], page.nav.pricing);
  setNodeText(navLinks[4], page.nav.demo);
  setText("#lang-dropdown-panel > div", page.interfaceLanguage);
  setText('a[href="Login.html"]', page.nav.login, 0);

  setText("#hero .inline-flex", page.hero.badge);
  setHtml("#hero h1", page.hero.title);
  setText("#hero p", page.hero.subtitle, 0);
  const heroButtons = document.querySelectorAll('#hero a[href="Login.html"], #hero a[href="#demo"]');
  setNodeText(heroButtons[0], page.hero.primaryCta);
  setNodeText(heroButtons[1], page.hero.secondaryCta);

  setHtml("#mission h2", page.mission.title);
  setText("#mission > div > div:first-child > p", page.mission.description);
  const statLabels = document.querySelectorAll("#mission .text-xs.uppercase");
  setNodeText(statLabels[0], page.mission.languages);
  setNodeText(statLabels[1], page.mission.accuracy);
  setNodeText(statLabels[2], page.mission.videos);

  const featureSection = document.querySelector("#features");
  if (featureSection) {
    const headings = featureSection.querySelectorAll("h2, h3");
    setNodeText(headings[0], page.features.title);
    setText("#features > div > p", page.features.subtitle);
    setNodeText(headings[1], page.features.voiceCloningTitle);
    setNodeText(headings[2], page.features.multilingualTitle);
    setNodeText(headings[3], page.features.exportTitle);
    const featureDescriptions = featureSection.querySelectorAll(".text-on-surface-variant");
    setNodeText(featureDescriptions[0], page.features.subtitle);
    setNodeText(featureDescriptions[1], page.features.voiceCloningDesc);
    setNodeText(featureDescriptions[2], page.features.multilingualDesc);
    setNodeText(featureDescriptions[3], page.features.exportDesc);
  }

  const howSection = document.querySelector('section a[href="workspace.html"]')?.closest("section");
  if (howSection) {
    const howTitles = howSection.querySelectorAll("h2, h3");
    setNodeText(howTitles[0], page.howItWorks.title);
    setNodeText(howTitles[1], page.howItWorks.step1Title);
    setNodeText(howTitles[2], page.howItWorks.step2Title);
    setNodeText(howTitles[3], page.howItWorks.step3Title);
    const howDescriptions = howSection.querySelectorAll("p.text-on-surface-variant");
    setNodeText(howDescriptions[0], page.howItWorks.step1Desc);
    setNodeText(howDescriptions[1], page.howItWorks.step2Desc);
    setNodeText(howDescriptions[2], page.howItWorks.step3Desc);
    setText('section a[href="workspace.html"]', page.howItWorks.button);
  }

  const testimonialTitle = document.querySelector('section h2.text-4xl.clash-display.font-bold.mb-16');
  setNodeText(testimonialTitle, page.testimonials.title);
  const testimonialParagraphs = testimonialTitle?.closest("section")?.querySelectorAll("p.text-on-surface-variant");
  if (testimonialParagraphs?.length >= 3) {
    setNodeText(testimonialParagraphs[0], page.testimonials.quote1);
    setNodeText(testimonialParagraphs[1], page.testimonials.quote2);
    setNodeText(testimonialParagraphs[2], page.testimonials.quote3);
  }

  const pricingSection = document.getElementById("pricing");
  if (pricingSection) {
    const pricingHeadings = pricingSection.querySelectorAll("h2");
    setNodeText(pricingHeadings[0], page.pricing.title);
    setText("#pricing > div > p", page.pricing.subtitle);
    const cards = pricingSection.querySelectorAll(".grid.md\\:grid-cols-3 > div");
    if (cards.length >= 3) {
      cards[0].innerHTML = page.pricing.starterCard;
      cards[1].innerHTML = page.pricing.proCard;
      cards[2].innerHTML = page.pricing.enterpriseCard;
    }
  }

  const ctaSection = document.querySelector('section a[href="Login.html"].bg-white');
  if (ctaSection) {
    const container = ctaSection.closest(".space-y-8");
    setNodeText(container?.querySelector("h2"), page.cta.title);
    setNodeText(container?.querySelector("p"), page.cta.description);
    setNodeText(container?.querySelector('a[href="Login.html"]'), page.cta.button);
  }

  const footerColumns = document.querySelectorAll("footer .space-y-6");
  if (footerColumns.length >= 4) {
    setText("footer .space-y-6 p", page.footer.description, 0);
    const footerText = footerColumns[0].querySelectorAll("div");
    setNodeText(footerText[1], page.footer.copyright);
    footerColumns[1].querySelector("h5").textContent = page.footer.productTitle;
    footerColumns[1].querySelector("div").innerHTML = page.footer.productLinks;
    footerColumns[2].querySelector("h5").textContent = page.footer.companyTitle;
    footerColumns[2].querySelector("div").innerHTML = page.footer.companyLinks;
    footerColumns[3].querySelector("h5").textContent = page.footer.connectTitle;
    footerColumns[3].querySelector("div").innerHTML = page.footer.connectLinks;
  }
}

function applyLoginPage(pack) {
  const page = pack.login;
  document.title = page.metaTitle;

  const featureHeadings = document.querySelectorAll("section.hidden h3");
  const featureDescriptions = document.querySelectorAll("section.hidden h3 + p");
  setNodeText(featureHeadings[0], page.leftFeature1Title);
  setNodeText(featureDescriptions[0], page.leftFeature1Desc);
  setNodeText(featureHeadings[1], page.leftFeature2Title);
  setNodeText(featureDescriptions[1], page.leftFeature2Desc);
  setNodeText(featureHeadings[2], page.leftFeature3Title);
  setNodeText(featureDescriptions[2], page.leftFeature3Desc);

  setText("header h1", page.heading);
  setText("header p", page.subheading);
  setText('label[for="email"]', page.emailLabel);
  setPlaceholder("#email", page.emailPlaceholder);
  setText("#emailErr", page.emailError);
  setText('label[for="password"]', page.passwordLabel);
  setText('a[href="#"]', page.forgotPassword, 0);
  setText("#pwErr", page.passwordError);
  setText('label[for="remember"]', page.rememberMe);
  setText("#loginBtnText", page.loginButton);
  setText(".relative.my-10 span", page.continueWith);

  const socialLabels = document.querySelectorAll("#googleBtn span.text-sm, #githubBtn span.text-sm");
  setNodeText(socialLabels[0], page.google);
  setNodeText(socialLabels[1], page.github);

  const footerParagraph = document.querySelector("footer p");
  if (footerParagraph) {
    footerParagraph.childNodes[0].textContent = `${page.noAccount} `;
    const signupLink = footerParagraph.querySelector("a");
    if (signupLink) {
      signupLink.textContent = page.createAccount;
    }
  }
}

function applyProfilePage(pack) {
  const page = pack.profile;
  document.title = page.metaTitle;

  const sidebarLinks = document.querySelectorAll("aside nav a");
  setText("aside .text-lg.font-bold", page.sidebar.brand);
  setText("aside .text-xs.uppercase", page.sidebar.tagline, 0);
  setText('aside a[href="workspace.html"].w-full', page.sidebar.newProject);
  setNodeText(sidebarLinks[0]?.querySelector("span:last-child"), page.sidebar.projects);
  setNodeText(sidebarLinks[1]?.querySelector("span:last-child"), page.sidebar.clones);
  setNodeText(sidebarLinks[2]?.querySelector("span:last-child"), page.sidebar.voiceLab);
  setNodeText(sidebarLinks[3]?.querySelector("span:last-child"), page.sidebar.templates);
  setNodeText(sidebarLinks[4]?.querySelector("span:last-child"), page.sidebar.profile);
  setNodeText(sidebarLinks[5]?.querySelector("span:last-child"), page.sidebar.settings);

  setText(".group\\/gopro .text-xs.font-bold.text-on-surface", page.sidebar.goPro);
  setText(".group\\/gopro .text-\\[10px\\].text-on-surface-variant", page.sidebar.unlockFullAccess);

  const mainHeadings = document.querySelectorAll("main h1, main h3");
  setNodeText(mainHeadings[0], page.heading);
  setText("#editProfileBtn", page.editProfile);
  setText('a[href="workspace.html"].inline-flex', page.quickAction);

  const detailLabels = document.querySelectorAll(".flex.justify-between.py-3.text-sm span:first-child");
  if (detailLabels.length >= 6) {
    setNodeText(detailLabels[0], page.details.fullName);
    setNodeText(detailLabels[1], page.details.email);
    setNodeText(detailLabels[2], page.details.username);
    setNodeText(detailLabels[3], page.details.timezone);
    setNodeText(detailLabels[4], page.details.interfaceLanguage);
    setNodeText(detailLabels[5], page.details.memberSince);
  }
  const detailValues = document.querySelectorAll(".flex.justify-between.py-3.text-sm span:last-child");
  if (detailValues.length >= 6) {
    setNodeText(detailValues[4], getCurrentLanguageLabel(pack));
  }

  setText('h3.text-sm.font-bold.font-headline', page.history.title);
  setText('h3.text-sm.font-bold.font-headline + p', page.history.subtitle);
  setPlaceholder("#histSearch", page.history.searchPlaceholder);
  setText("#filterLabel", page.history.filterAll);

  setText("#modalTitle", page.modal.title);
  setText('label[for="edit-name"]', page.modal.fullName);
  setText('label[for="edit-email"]', page.modal.email);
  setText('label[for="edit-username"]', page.modal.username);
  setText('label[for="edit-timezone"]', page.modal.timezone);
  setText("#modalCancel", page.modal.cancel);
  setText("#saveBtnText", page.modal.saveChanges);

  patchProfileDynamicContent(pack);
}

function patchProfileDynamicContent(pack) {
  const page = pack.profile;
  window.statusHTML = function statusHTML(status) {
    if (status === "done") {
      return `<span class="inline-flex items-center gap-1 text-xs font-medium text-tertiary bg-tertiary/10 px-3 py-1 rounded-full"><span class="material-symbols-outlined text-xs" style="font-variation-settings:'FILL' 1;">check_circle</span>${page.history.statusDone}</span>`;
    }
    if (status === "processing") {
      return `<span class="inline-flex items-center gap-1 text-xs font-medium text-secondary bg-secondary/10 px-3 py-1 rounded-full"><span class="w-1.5 h-1.5 rounded-full bg-secondary pulse-dot"></span>${page.history.statusProcessing}</span>`;
    }
    return `<span class="inline-flex items-center gap-1 text-xs font-medium text-error bg-error/10 px-3 py-1 rounded-full"><span class="material-symbols-outlined text-xs" style="font-variation-settings:'FILL' 1;">cancel</span>${page.history.statusFailed}</span>`;
  };

  if (typeof window.renderTable === "function") {
    window.renderTable();
  }

  const filterOptions = document.querySelectorAll(".filter-opt");
  if (filterOptions.length >= 4) {
    filterOptions[0].textContent = page.history.filterAll;
    filterOptions[1].textContent = page.history.filterDone;
    filterOptions[2].textContent = page.history.filterProcessing;
    filterOptions[3].textContent = page.history.filterFailed;
  }
}

function applyWorkspacePage(pack) {
  const page = pack.workspace;
  document.title = page.metaTitle;

  const sidebarLinks = document.querySelectorAll("aside nav a");
  setText("aside .text-lg.font-bold", page.sidebar.brand);
  setText("aside .text-xs.uppercase", page.sidebar.tagline, 0);
  setText("#newProjectBtn", page.sidebar.newProject);
  setNodeText(sidebarLinks[0]?.querySelector("span:last-child"), page.sidebar.projects);
  setNodeText(sidebarLinks[1]?.querySelector("span:last-child"), page.sidebar.clones);
  setNodeText(sidebarLinks[2]?.querySelector("span:last-child"), page.sidebar.voiceLab);
  setNodeText(sidebarLinks[3]?.querySelector("span:last-child"), page.sidebar.templates);
  setNodeText(sidebarLinks[4]?.querySelector("span:last-child"), page.sidebar.settings);
  setNodeText(sidebarLinks[5]?.querySelector("span:last-child"), page.sidebar.profile);

  const statusTexts = document.querySelectorAll(".mb-12 .text-xs");
  if (statusTexts.length >= 6) {
    setNodeText(statusTexts[0], page.status.idle);
    setNodeText(statusTexts[1], page.status.uploading);
    setNodeText(statusTexts[2], page.status.cloning);
    setNodeText(statusTexts[3], page.status.translating);
    setNodeText(statusTexts[4], page.status.rendering);
    setNodeText(statusTexts[5], page.status.done);
  }

  const panels = document.querySelectorAll("main h2.text-2xl");
  setNodeText(panels[0], page.source.title);
  setNodeText(panels[1], page.output.title);
  setText("#dropZoneTitle", page.source.dropTitle);
  setText("#dropZoneSubtitle", getWorkspaceDropSubtitle(page));
  setText("#browseBtn", page.source.browseFiles);
  const sourceLabels = document.querySelectorAll(".grid.grid-cols-2 .text-\\[10px\\]");
  setNodeText(sourceLabels[0], page.source.originalLanguage);
  setNodeText(sourceLabels[1], page.source.targetLanguage);
  setText("#startDubbingBtn", page.source.startDubbing);
  setText("#procStageText", page.output.processingTitle);
  setText("#procEtaText", page.output.processingEta);

  const scriptLabel = document.querySelector('label.flex.items-center.justify-between span');
  const editScriptLink = document.querySelector('label.flex.items-center.justify-between .text-secondary');
  setNodeText(scriptLabel, page.output.translatedScript);
  setNodeText(editScriptLink, page.output.editScript);
  setPlaceholder("#scriptArea", page.output.scriptPlaceholder);

  const actionButtons = document.querySelectorAll("#downloadBtn, #shareBtn");
  setNodeText(actionButtons[0], page.output.downloadVideo);
  setNodeText(actionButtons[1], page.output.share);
  setText("#notifToast .text-sm.font-bold", page.toast.title);
  setText("#notifToast .text-xs", page.toast.description);
  setNodeText(document.querySelector("#originalLanguage option[value='auto']"), getAutoDetectLabel());

  if (workspaceInitialized) {
    refreshWorkspaceState();
  }
}

function initializeCurrentPage() {
  const page = document.body.dataset.page;
  if (page === "login") {
    initLoginPage();
  }
  if (page === "workspace") {
    initWorkspacePage();
  }
}

function initLoginPage() {
  if (loginInitialized) {
    return;
  }

  const form = document.getElementById("loginForm");
  const emailInput = document.getElementById("email");
  const passwordInput = document.getElementById("password");
  const emailErr = document.getElementById("emailErr");
  const passwordErr = document.getElementById("pwErr");
  const loginButton = document.getElementById("loginBtn");
  const spinner = document.getElementById("loginSpinner");
  const pwToggle = document.getElementById("pwToggle");
  const pwToggleIcon = document.getElementById("pwToggleIcon");
  const socialButtons = [document.getElementById("googleBtn"), document.getElementById("githubBtn")];

  if (!form || !emailInput || !passwordInput || !loginButton) {
    return;
  }

  loginInitialized = true;

  const clearErrors = () => {
    emailErr?.classList.add("hidden");
    passwordErr?.classList.add("hidden");
  };

  const setBusy = (isBusy) => {
    loginButton.disabled = isBusy;
    loginButton.classList.toggle("opacity-70", isBusy);
    loginButton.classList.toggle("cursor-not-allowed", isBusy);
    spinner?.classList.toggle("hidden", !isBusy);
  };

  pwToggle?.addEventListener("click", () => {
    const showPassword = passwordInput.type === "password";
    passwordInput.type = showPassword ? "text" : "password";
    if (pwToggleIcon) {
      pwToggleIcon.textContent = showPassword ? "visibility" : "visibility_off";
    }
  });

  emailInput.addEventListener("input", clearErrors);
  passwordInput.addEventListener("input", clearErrors);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    clearErrors();

    const email = emailInput.value.trim();
    const password = passwordInput.value;
    let hasError = false;

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      emailErr?.classList.remove("hidden");
      hasError = true;
    }

    if (password.length < 6) {
      passwordErr?.classList.remove("hidden");
      hasError = true;
    }

    if (hasError) {
      return;
    }

    setBusy(true);
    window.setTimeout(() => {
      window.location.href = LOGIN_REDIRECT_URL;
    }, 650);
  });

  socialButtons.forEach((button) => {
    button?.addEventListener("click", () => {
      setBusy(true);
      window.setTimeout(() => {
        window.location.href = LOGIN_REDIRECT_URL;
      }, 450);
    });
  });
}

function initWorkspacePage() {
  if (workspaceInitialized) {
    return;
  }

  const elements = getWorkspaceElements();
  if (!elements.dropZone || !elements.fileInput || !elements.startDubbingBtn) {
    return;
  }

  workspaceInitialized = true;
  workspaceState.elements = elements;

  elements.originalLanguage.value = "auto";
  elements.originalLanguage.disabled = true;
  elements.originalLanguage.title = "The current backend auto-detects the source language.";
  elements.originalLanguage.closest("div")?.classList.add("opacity-70");

  elements.browseBtn?.addEventListener("click", (event) => {
    event.preventDefault();
    if (!elements.fileInput.disabled) {
      elements.fileInput.click();
    }
  });

  elements.dropZone.addEventListener("click", (event) => {
    if (elements.fileInput.disabled) {
      return;
    }
    if (event.target.closest("#browseBtn") || event.target.closest("video")) {
      return;
    }
    elements.fileInput.click();
  });

  elements.fileInput.addEventListener("change", () => {
    const file = elements.fileInput.files?.[0];
    if (file) {
      handleSelectedSourceFile(file);
    }
  });

  elements.dropZone.addEventListener("dragover", (event) => {
    if (elements.fileInput.disabled) {
      return;
    }
    event.preventDefault();
    elements.dropZone.classList.add("border-primary/50", "bg-primary/5");
  });

  elements.dropZone.addEventListener("dragleave", () => {
    elements.dropZone.classList.remove("border-primary/50", "bg-primary/5");
  });

  elements.dropZone.addEventListener("drop", (event) => {
    if (elements.fileInput.disabled) {
      return;
    }
    event.preventDefault();
    elements.dropZone.classList.remove("border-primary/50", "bg-primary/5");
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      handleSelectedSourceFile(file);
    }
  });

  elements.startDubbingBtn.addEventListener("click", () => {
    void runWorkspaceDubbing();
  });

  elements.downloadBtn?.addEventListener("click", () => {
    triggerOutputDownload();
  });

  elements.shareBtn?.addEventListener("click", () => {
    void shareOutputVideo();
  });

  elements.closeToast?.addEventListener("click", hideToast);
  elements.newProjectBtn?.addEventListener("click", resetWorkspace);

  resetWorkspace();
}

function getWorkspaceElements() {
  return {
    browseBtn: document.getElementById("browseBtn"),
    closeToast: document.getElementById("closeToast"),
    downloadBtn: document.getElementById("downloadBtn"),
    dropZone: document.getElementById("dropZone"),
    dropZoneIcon: document.getElementById("dropZoneIcon"),
    dropZoneSubtitle: document.getElementById("dropZoneSubtitle"),
    dropZoneTitle: document.getElementById("dropZoneTitle"),
    fileInput: document.getElementById("fileInput"),
    newProjectBtn: document.getElementById("newProjectBtn"),
    notifToast: document.getElementById("notifToast"),
    originalLanguage: document.getElementById("originalLanguage"),
    outputPlaceholderImage: document.getElementById("outputPlaceholderImage"),
    outputVideoPreview: document.getElementById("outputVideoPreview"),
    procEtaText: document.getElementById("procEtaText"),
    procStageText: document.getElementById("procStageText"),
    processingBadgeDot: document.getElementById("processingBadgeDot"),
    processingBadgeLabel: document.getElementById("processingBadgeLabel"),
    processingOverlay: document.getElementById("processingOverlay"),
    scriptArea: document.getElementById("scriptArea"),
    shareBtn: document.getElementById("shareBtn"),
    sourcePlaceholderImage: document.getElementById("sourcePlaceholderImage"),
    sourceVideoPreview: document.getElementById("sourceVideoPreview"),
    startDubbingBtn: document.getElementById("startDubbingBtn"),
    targetLanguage: document.getElementById("targetLanguage"),
    uploadBar: document.getElementById("uploadBar")
  };
}

function resetWorkspace() {
  const elements = workspaceState.elements;
  if (!elements) {
    return;
  }

  stopWorkspaceTicker();
  hideToast();
  workspaceState.phase = "idle";
  workspaceState.stageIndex = 0;
  workspaceState.sourceFile = null;
  workspaceState.outputBlob = null;
  workspaceState.outputFilename = "";

  revokeObjectUrl("sourcePreviewUrl");
  revokeObjectUrl("outputPreviewUrl");

  if (elements.fileInput) {
    elements.fileInput.value = "";
  }
  if (elements.sourceVideoPreview) {
    elements.sourceVideoPreview.pause();
    elements.sourceVideoPreview.removeAttribute("src");
    elements.sourceVideoPreview.load();
    elements.sourceVideoPreview.classList.add("hidden");
  }
  elements.sourcePlaceholderImage?.classList.remove("hidden");
  if (elements.outputVideoPreview) {
    elements.outputVideoPreview.pause();
    elements.outputVideoPreview.removeAttribute("src");
    elements.outputVideoPreview.load();
    elements.outputVideoPreview.classList.add("hidden");
  }
  elements.outputPlaceholderImage?.classList.remove("hidden");
  elements.processingOverlay?.classList.add("hidden");

  if (elements.scriptArea) {
    elements.scriptArea.value = "";
    elements.scriptArea.placeholder = getWorkspacePack().output.scriptPlaceholder;
  }

  if (elements.dropZoneIcon) {
    elements.dropZoneIcon.textContent = "cloud_upload";
  }
  if (elements.dropZoneTitle) {
    elements.dropZoneTitle.textContent = getWorkspacePack().source.dropTitle;
  }
  if (elements.dropZoneSubtitle) {
    elements.dropZoneSubtitle.textContent = getWorkspaceDropSubtitle();
  }
  if (elements.uploadBar) {
    elements.uploadBar.style.width = "0%";
  }
  if (elements.procStageText) {
    elements.procStageText.textContent = getWorkspacePack().output.processingTitle;
  }
  if (elements.procEtaText) {
    elements.procEtaText.textContent = getWorkspacePack().output.processingEta;
  }

  setButtonEnabled(elements.startDubbingBtn, false);
  setButtonEnabled(elements.downloadBtn, false);
  setButtonEnabled(elements.shareBtn, false);
  setBadgeState("idle");
  toggleWorkspaceInputs(false);
}

function refreshWorkspaceState() {
  const elements = workspaceState.elements;
  if (!elements) {
    return;
  }

  setNodeText(document.querySelector("#originalLanguage option[value='auto']"), getAutoDetectLabel());

  if (!workspaceState.sourceFile) {
    if (elements.dropZoneTitle) {
      elements.dropZoneTitle.textContent = getWorkspacePack().source.dropTitle;
    }
    if (elements.dropZoneSubtitle) {
      elements.dropZoneSubtitle.textContent = getWorkspaceDropSubtitle();
    }
    if (elements.dropZoneIcon) {
      elements.dropZoneIcon.textContent = "cloud_upload";
    }
  }

  if (workspaceState.phase === "processing") {
    updateProcessingStageText();
  } else {
    setBadgeState(workspaceState.phase);
  }
}

function handleSelectedSourceFile(file) {
  if (!isSupportedVideoFile(file)) {
    showToast("Unsupported file", "Please choose an MP4, MOV, or WEBM video file.");
    return;
  }

  const elements = workspaceState.elements;
  if (!elements) {
    return;
  }

  workspaceState.sourceFile = file;
  workspaceState.phase = "idle";
  workspaceState.outputBlob = null;
  workspaceState.outputFilename = "";

  revokeObjectUrl("sourcePreviewUrl");
  revokeObjectUrl("outputPreviewUrl");

  clearOutputPreview();

  workspaceState.sourcePreviewUrl = URL.createObjectURL(file);
  elements.sourceVideoPreview.src = workspaceState.sourcePreviewUrl;
  elements.sourceVideoPreview.classList.remove("hidden");
  elements.sourcePlaceholderImage?.classList.add("hidden");

  if (elements.dropZoneIcon) {
    elements.dropZoneIcon.textContent = "video_file";
  }
  if (elements.dropZoneTitle) {
    elements.dropZoneTitle.textContent = file.name;
  }
  if (elements.dropZoneSubtitle) {
    elements.dropZoneSubtitle.textContent = `${formatBytes(file.size)} ready to dub`;
  }
  if (elements.uploadBar) {
    elements.uploadBar.style.width = "100%";
  }
  if (elements.scriptArea) {
    elements.scriptArea.value = "";
    elements.scriptArea.placeholder = getWorkspacePack().output.scriptPlaceholder;
  }

  setButtonEnabled(elements.startDubbingBtn, true);
  setBadgeState("idle");
  hideToast();
}

function clearOutputPreview() {
  const elements = workspaceState.elements;
  if (!elements) {
    return;
  }

  if (elements.outputVideoPreview) {
    elements.outputVideoPreview.pause();
    elements.outputVideoPreview.removeAttribute("src");
    elements.outputVideoPreview.load();
    elements.outputVideoPreview.classList.add("hidden");
  }
  elements.outputPlaceholderImage?.classList.remove("hidden");
  elements.processingOverlay?.classList.add("hidden");

  workspaceState.outputBlob = null;
  workspaceState.outputFilename = "";
  revokeObjectUrl("outputPreviewUrl");

  setButtonEnabled(elements.downloadBtn, false);
  setButtonEnabled(elements.shareBtn, false);
}

async function runWorkspaceDubbing() {
  const elements = workspaceState.elements;
  if (!elements) {
    return;
  }

  if (!workspaceState.sourceFile) {
    showToast("Video required", "Choose a source video before starting the dubbing job.");
    return;
  }

  const targetLanguage = elements.targetLanguage?.value || "en";
  const targetLabel = getSelectedLabel(elements.targetLanguage);
  const formData = new FormData();
  formData.append("file", workspaceState.sourceFile);

  clearOutputPreview();
  hideToast();
  toggleWorkspaceInputs(true);
  setButtonEnabled(elements.startDubbingBtn, false);
  workspaceState.phase = "processing";
  workspaceState.stageIndex = 0;
  elements.processingOverlay?.classList.remove("hidden");
  if (elements.scriptArea) {
    elements.scriptArea.value = `Generating a ${targetLabel} dub for ${workspaceState.sourceFile.name}...`;
  }

  updateProcessingStageText();
  startWorkspaceTicker();

  try {
    const response = await fetch(`${API_BASE_URL}/process_video/?target_language=${encodeURIComponent(targetLanguage)}`, {
      method: "POST",
      body: formData
    });

    if (!response.ok) {
      throw new Error(await extractErrorMessage(response));
    }

    const outputBlob = await response.blob();
    if (!outputBlob.size) {
      throw new Error("The backend returned an empty video file.");
    }

    workspaceState.outputBlob = outputBlob;
    workspaceState.outputFilename = buildOutputFilename(workspaceState.sourceFile.name, targetLanguage);
    workspaceState.outputPreviewUrl = URL.createObjectURL(outputBlob);

    elements.outputVideoPreview.src = workspaceState.outputPreviewUrl;
    elements.outputVideoPreview.classList.remove("hidden");
    elements.outputPlaceholderImage?.classList.add("hidden");

    workspaceState.phase = "done";
    stopWorkspaceTicker();
    elements.processingOverlay?.classList.add("hidden");
    setBadgeState("done");
    setButtonEnabled(elements.downloadBtn, true);
    setButtonEnabled(elements.shareBtn, true);
    setButtonEnabled(elements.startDubbingBtn, true);
    toggleWorkspaceInputs(false);

    if (elements.scriptArea) {
      elements.scriptArea.value = [
        `Dubbed video ready in ${targetLabel}.`,
        `Source file: ${workspaceState.sourceFile.name}`,
        `Output file: ${workspaceState.outputFilename}`,
        "",
        "The current backend returns the final video only, so a separate translated script preview is not available in this workspace yet."
      ].join("\n");
    }

    showToast(getWorkspacePack().toast.title, `${workspaceState.outputFilename} is ready to review and download.`);
  } catch (error) {
    workspaceState.phase = "error";
    stopWorkspaceTicker();
    elements.processingOverlay?.classList.add("hidden");
    setBadgeState("error");
    setButtonEnabled(elements.startDubbingBtn, true);
    toggleWorkspaceInputs(false);

    if (elements.scriptArea) {
      elements.scriptArea.value = `Processing failed.\n\n${error.message || "Something went wrong while calling the dubbing backend."}`;
    }

    showToast("Processing failed", error.message || "Something went wrong while calling the dubbing backend.");
  }
}

function toggleWorkspaceInputs(disabled) {
  const elements = workspaceState.elements;
  if (!elements) {
    return;
  }

  elements.fileInput.disabled = disabled;
  if (elements.targetLanguage) {
    elements.targetLanguage.disabled = disabled;
  }
  if (elements.browseBtn) {
    elements.browseBtn.disabled = disabled;
    elements.browseBtn.classList.toggle("opacity-50", disabled);
    elements.browseBtn.classList.toggle("cursor-not-allowed", disabled);
  }
  if (elements.newProjectBtn) {
    elements.newProjectBtn.disabled = disabled;
    elements.newProjectBtn.classList.toggle("opacity-70", disabled);
    elements.newProjectBtn.classList.toggle("cursor-not-allowed", disabled);
  }
}

function startWorkspaceTicker() {
  stopWorkspaceTicker();
  workspaceState.stageTimer = window.setInterval(() => {
    workspaceState.stageIndex = (workspaceState.stageIndex + 1) % WORKSPACE_STAGE_KEYS.length;
    updateProcessingStageText();
  }, 2200);
}

function stopWorkspaceTicker() {
  if (workspaceState.stageTimer) {
    window.clearInterval(workspaceState.stageTimer);
    workspaceState.stageTimer = null;
  }
}

function updateProcessingStageText() {
  const elements = workspaceState.elements;
  if (!elements) {
    return;
  }

  const stageKey = WORKSPACE_STAGE_KEYS[workspaceState.stageIndex] || "rendering";
  const stageLabel = getStageLabel(stageKey);

  if (elements.procStageText) {
    elements.procStageText.textContent = stageLabel;
  }
  if (elements.procEtaText) {
    elements.procEtaText.textContent = getWorkspacePack().output.processingEta;
  }

  setBadgeState("processing", stageLabel);
}

function setBadgeState(phase, labelOverride = "") {
  const elements = workspaceState.elements;
  if (!elements?.processingBadgeDot || !elements.processingBadgeLabel) {
    return;
  }

  elements.processingBadgeDot.classList.remove("bg-outline", "bg-secondary", "bg-tertiary", "bg-error", "animate-pulse");
  elements.processingBadgeLabel.classList.remove(
    "text-on-surface-variant",
    "bg-white/5",
    "text-secondary",
    "bg-secondary/10",
    "text-tertiary",
    "bg-tertiary/10",
    "text-error",
    "bg-error/10"
  );

  if (phase === "processing") {
    elements.processingBadgeDot.classList.add("bg-secondary", "animate-pulse");
    elements.processingBadgeLabel.classList.add("text-secondary", "bg-secondary/10");
    elements.processingBadgeLabel.textContent = labelOverride || getStageLabel(WORKSPACE_STAGE_KEYS[workspaceState.stageIndex]);
    return;
  }

  if (phase === "done") {
    elements.processingBadgeDot.classList.add("bg-tertiary");
    elements.processingBadgeLabel.classList.add("text-tertiary", "bg-tertiary/10");
    elements.processingBadgeLabel.textContent = labelOverride || getWorkspacePack().status.done;
    return;
  }

  if (phase === "error") {
    elements.processingBadgeDot.classList.add("bg-error");
    elements.processingBadgeLabel.classList.add("text-error", "bg-error/10");
    elements.processingBadgeLabel.textContent = labelOverride || "Error";
    return;
  }

  elements.processingBadgeDot.classList.add("bg-outline");
  elements.processingBadgeLabel.classList.add("text-on-surface-variant", "bg-white/5");
  elements.processingBadgeLabel.textContent = labelOverride || getWorkspacePack().status.idle;
}

function setButtonEnabled(button, enabled) {
  if (!button) {
    return;
  }
  button.disabled = !enabled;
  button.classList.toggle("opacity-50", !enabled);
  button.classList.toggle("cursor-not-allowed", !enabled);
}

function showToast(title, description) {
  const toast = workspaceState.elements?.notifToast;
  if (!toast) {
    return;
  }

  window.clearTimeout(workspaceState.toastTimer);
  setText("#notifToast .text-sm.font-bold", title);
  setText("#notifToast .text-xs", description);
  toast.classList.remove("opacity-0", "translate-y-6", "pointer-events-none");
  workspaceState.toastTimer = window.setTimeout(() => {
    hideToast();
  }, 4500);
}

function hideToast() {
  const toast = workspaceState.elements?.notifToast;
  if (!toast) {
    return;
  }
  window.clearTimeout(workspaceState.toastTimer);
  toast.classList.add("opacity-0", "translate-y-6", "pointer-events-none");
}

function triggerOutputDownload() {
  if (!workspaceState.outputBlob || !workspaceState.outputFilename) {
    return;
  }

  const downloadUrl = workspaceState.outputPreviewUrl || URL.createObjectURL(workspaceState.outputBlob);
  const anchor = document.createElement("a");
  anchor.href = downloadUrl;
  anchor.download = workspaceState.outputFilename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

async function shareOutputVideo() {
  if (!workspaceState.outputBlob || !workspaceState.outputFilename) {
    return;
  }

  const shareFile = new File([workspaceState.outputBlob], workspaceState.outputFilename, { type: "video/mp4" });

  try {
    if (navigator.share && navigator.canShare?.({ files: [shareFile] })) {
      await navigator.share({
        files: [shareFile],
        title: "Multiva dubbed video"
      });
      return;
    }

    triggerOutputDownload();
    showToast("Download started", "Sharing is not available in this browser yet, so the video download has started instead.");
  } catch (error) {
    if (error?.name !== "AbortError") {
      showToast("Share unavailable", "The browser could not share this video. Use the download button instead.");
    }
  }
}

async function extractErrorMessage(response) {
  const contentType = response.headers.get("content-type") || "";

  if (contentType.includes("application/json")) {
    const payload = await response.json();
    return payload.detail || payload.message || `Request failed with status ${response.status}`;
  }

  const text = await response.text();
  return text.trim() || `Request failed with status ${response.status}`;
}

function getWorkspacePack() {
  return getCurrentPack().workspace || fallbackEnglish().workspace;
}

function getCurrentPack() {
  const dictionary = translationCache || { defaultLanguage: "en", languages: { en: fallbackEnglish() } };
  return getLanguagePack(currentLanguage, dictionary);
}

function getWorkspaceDropSubtitle(page = getWorkspacePack()) {
  const rawSubtitle = page.source?.dropSubtitle || "MP4, MOV, or WEBM";
  return rawSubtitle.replace(/\s*\([^)]*\)\s*$/, "").trim();
}

function getAutoDetectLabel() {
  return "Auto Detect";
}

function getStageLabel(stageKey) {
  const status = getWorkspacePack().status || {};
  return status[stageKey] || stageKey;
}

function getSelectedLabel(select) {
  return select?.options?.[select.selectedIndex]?.textContent || select?.value || "";
}

function buildOutputFilename(sourceName, targetLanguage) {
  const baseName = sourceName.replace(/\.[^.]+$/, "");
  return `${baseName}_dubbed_${targetLanguage}.mp4`;
}

function isSupportedVideoFile(file) {
  const lowerName = file.name.toLowerCase();
  return file.type.startsWith("video/") || [".mp4", ".mov", ".webm"].some((ext) => lowerName.endsWith(ext));
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }

  const units = ["B", "KB", "MB", "GB"];
  const unitIndex = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / (1024 ** unitIndex);
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function revokeObjectUrl(key) {
  if (workspaceState[key]) {
    URL.revokeObjectURL(workspaceState[key]);
    workspaceState[key] = null;
  }
}

function setText(selector, value, index = null) {
  if (index === null) {
    const element = document.querySelector(selector);
    setNodeText(element, value);
    return;
  }
  const elements = document.querySelectorAll(selector);
  setNodeText(elements[index], value);
}

function setHtml(selector, value) {
  const element = document.querySelector(selector);
  if (element && typeof value === "string") {
    element.innerHTML = value;
  }
}

function setPlaceholder(selector, value) {
  const element = document.querySelector(selector);
  if (element) {
    element.placeholder = value;
  }
}

function setNodeText(node, value) {
  if (node && typeof value === "string") {
    node.textContent = value;
  }
}

function getCurrentLanguageLabel(pack) {
  const selectedOption = document.querySelector(`.lang-option[data-lang-code="${currentLanguage}"]`);
  return selectedOption?.dataset.lang || pack.languageLabel;
}

function fallbackEnglish() {
  return {
    languageLabel: "English",
    index: {
      metaTitle: "Multiva.AI — AI Voice Engine",
      nav: { home: "Home", mission: "Mission", features: "Features", pricing: "Pricing", demo: "Demo", login: "Login" },
      interfaceLanguage: "Interface Language",
      hero: { badge: "AI-Powered Voice Cloning", title: "Your Voice.<br>Every Language.", subtitle: "Clone your unique voice in seconds. Dub your content into 50+ languages with emotion and accuracy.", primaryCta: "Get Started Free", secondaryCta: "Watch Demo" },
      mission: { title: "Breaking language barriers — <span class=\"text-tertiary\">one voice</span> at a time.", description: "We help creators and teams localize content without losing identity, tone, or emotional nuance.", languages: "Languages", accuracy: "Accuracy", videos: "Videos Dubbed" },
      features: { title: "Unmatched Audio Quality", subtitle: "Everything you need to scale your content globally.", voiceCloningTitle: "Voice Cloning", voiceCloningDesc: "Clone any voice in seconds while preserving tone and expression.", multilingualTitle: "Multilingual Dubbing", multilingualDesc: "Translate and synchronize content across many languages automatically.", exportTitle: "Instant Export", exportDesc: "Export polished results for YouTube, TikTok, courses, and more." },
      howItWorks: { title: "How It Works", step1Title: "Upload", step1Desc: "Upload your video or audio file.", step2Title: "Select", step2Desc: "Choose language and voice settings.", step3Title: "Download", step3Desc: "Get your final dubbed content in minutes.", button: "Open Workspace" },
      testimonials: { title: "Trusted by Creators", quote1: "The voice cloning sounds incredibly close to the real speaker.", quote2: "We launched our course in 12 languages without rebuilding the workflow.", quote3: "It is the first tool we used that feels emotionally aware." },
      pricing: { title: "Simple, transparent pricing.", subtitle: "Start free. Scale as you grow.", starterCard: "", proCard: "", enterpriseCard: "" },
      cta: { title: "Ready to go global?", description: "Join creators using Multiva.AI to reach audiences everywhere.", button: "Start Free" },
      footer: { description: "The world's most advanced AI voice engine for global creators and enterprises.", copyright: "© 2026 Multiva.AI. All rights reserved.", productTitle: "Product", companyTitle: "Company", connectTitle: "Connect", productLinks: "", companyLinks: "", connectLinks: "" }
    },
    login: {
      metaTitle: "Multiva.AI — Login",
      heading: "Welcome back",
      subheading: "Continue your creative journey with Multiva.AI.",
      leftFeature1Title: "Studio-grade dubbing",
      leftFeature1Desc: "Create multilingual videos without losing the original voice identity.",
      leftFeature2Title: "Fast turnaround",
      leftFeature2Desc: "Process source videos and review output in one streamlined workspace.",
      leftFeature3Title: "Global reach",
      leftFeature3Desc: "Deliver the same story to audiences across languages.",
      emailLabel: "Email Address",
      emailPlaceholder: "name@company.com",
      emailError: "Please enter a valid email address.",
      passwordLabel: "Password",
      forgotPassword: "Forgot password?",
      passwordError: "Password must be at least 6 characters.",
      rememberMe: "Keep me signed in for 30 days",
      loginButton: "Login",
      continueWith: "Or continue with",
      google: "Google",
      github: "GitHub",
      noAccount: "Don't have an account?",
      createAccount: "Create account"
    },
    profile: {
      metaTitle: "Multiva.AI — My Profile",
      sidebar: {
        brand: "Workspace",
        tagline: "AI Voice Engine",
        newProject: "New Project",
        projects: "Projects",
        clones: "Clones",
        voiceLab: "Voice Lab",
        templates: "Templates",
        profile: "Profile",
        settings: "Settings",
        goPro: "Go Pro",
        unlockFullAccess: "Unlock full access"
      },
      heading: "Profile",
      editProfile: "Edit Profile",
      quickAction: "Open Workspace",
      details: {
        fullName: "Full Name",
        email: "Email",
        username: "Username",
        timezone: "Timezone",
        interfaceLanguage: "Interface Language",
        memberSince: "Member Since"
      },
      history: {
        title: "History",
        subtitle: "Review previous dubbing jobs.",
        searchPlaceholder: "Search projects",
        filterAll: "All",
        filterDone: "Done",
        filterProcessing: "Processing",
        filterFailed: "Failed",
        statusDone: "Done",
        statusProcessing: "Processing",
        statusFailed: "Failed"
      },
      modal: {
        title: "Edit Profile",
        fullName: "Full Name",
        email: "Email",
        username: "Username",
        timezone: "Timezone",
        cancel: "Cancel",
        saveChanges: "Save Changes"
      }
    },
    workspace: {
      metaTitle: "Multiva.AI | AI Voice Engine Dashboard",
      sidebar: {
        brand: "Workspace",
        tagline: "AI Voice Engine",
        newProject: "New Project",
        projects: "Projects",
        clones: "Clones",
        voiceLab: "Voice Lab",
        templates: "Templates",
        settings: "Settings",
        profile: "Profile"
      },
      status: {
        idle: "Idle",
        uploading: "Uploading",
        cloning: "Cloning",
        translating: "Translating",
        rendering: "Rendering",
        done: "Done"
      },
      source: {
        title: "Source Video",
        dropTitle: "Drag video here to start",
        dropSubtitle: "MP4, MOV, or WEBM",
        browseFiles: "Browse Files",
        originalLanguage: "Original Language",
        targetLanguage: "Target Language",
        startDubbing: "Start Dubbing"
      },
      output: {
        title: "Dubbed Output",
        processingTitle: "Synthesizing Voice Texture",
        processingEta: "Estimated time: 14s",
        translatedScript: "Translated Script",
        editScript: "Edit Script",
        scriptPlaceholder: "Processing notes will appear here.",
        downloadVideo: "Download Video",
        share: "Share"
      },
      toast: {
        title: "Voice Clone Ready",
        description: "Your dubbed video has been generated."
      }
    }
  };
}
