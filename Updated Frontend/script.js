const I18N_STORAGE_KEY = "multiva-language";
const RTL_LANGS = new Set(["ar"]);
const KNOWN_LANGUAGE_CODES = new Set(["en", "hi", "es", "fr", "ja", "de", "ar", "tr", "ru", "pt", "it", "ko", "nl", "cs"]);

const PAGE_APPLIERS = {
  index: applyIndexPage,
  login: applyLoginPage,
  profile: applyProfilePage,
  workspace: applyWorkspacePage
};

let translationCache = null;
let currentLanguage = "en";

document.addEventListener("DOMContentLoaded", async () => {
  translationCache = await loadTranslations();
  currentLanguage = getSavedLanguage(translationCache);
  bindLanguageDropdown(translationCache);
  applyLanguage(currentLanguage, translationCache);
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
  setText('a[href="login.html"]', page.nav.login, 0);

  setText("#hero .inline-flex", page.hero.badge);
  setHtml("#hero h1", page.hero.title);
  setText("#hero p", page.hero.subtitle, 0);
  const heroButtons = document.querySelectorAll('#hero a[href="login.html"], #hero a[href="#demo"]');
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

  const ctaSection = document.querySelector('section a[href="login.html"].bg-white');
  if (ctaSection) {
    const container = ctaSection.closest(".space-y-8");
    setNodeText(container?.querySelector("h2"), page.cta.title);
    setNodeText(container?.querySelector("p"), page.cta.description);
    setNodeText(container?.querySelector('a[href="login.html"]'), page.cta.button);
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
  setText("#dropZoneSubtitle", page.source.dropSubtitle);
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

  const actionButtons = document.querySelectorAll("#downloadBtn, .flex.gap-4 button:last-child");
  setNodeText(actionButtons[0], page.output.downloadVideo);
  setNodeText(actionButtons[1], page.output.share);
  setText("#notifToast .text-sm.font-bold", page.toast.title);
  setText("#notifToast .text-xs", page.toast.description);
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
    }
  };
}
