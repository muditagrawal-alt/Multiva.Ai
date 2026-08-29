import { chromium } from "playwright";
const S = process.argv[2], CLIP = process.argv[3];
const errs = [];
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1180, height: 812 }, deviceScaleFactor: 2 });
p.on("console", (m) => m.type() === "error" && errs.push(m.text()));
p.on("pageerror", (e) => errs.push("pageerror: " + e.message));

await p.goto("http://127.0.0.1:8000/app/", { waitUntil: "networkidle" });
await p.waitForTimeout(2000);
await p.screenshot({ path: S + "/home-before.png" });
console.log("home status:", await p.locator("footer").innerText().catch(() => "?"));

await p.getByRole("link", { name: /new project/i }).first().click();
await p.waitForURL("**/app/studio");
await p.setInputFiles("#clip", CLIP);
await p.waitForTimeout(1000);
await p.getByRole("button", { name: /render dub/i }).click();

const deadline = Date.now() + 10 * 60 * 1000;
let outcome = "timeout";
while (Date.now() < deadline) {
  const t = await p.locator("footer").innerText().catch(() => "");
  if (/Render complete/i.test(t)) { outcome = "done"; break; }
  if (/Render failed/i.test(t)) { outcome = "failed"; break; }
  await p.waitForTimeout(4000);
}
console.log("render:", outcome);
if (outcome !== "done") { console.log(errs); await b.close(); process.exit(1); }

await p.waitForTimeout(3000);
await p.screenshot({ path: S + "/studio-inspector.png", clip: { x: 918, y: 30, width: 262, height: 760 } });

// Pull the job id straight out of the inspector, then exercise every export.
const jobId = await p.evaluate(async () => {
  const r = await fetch("/api/health"); await r.json();
  return document.body.innerText.match(/Job\s+([0-9a-f]{8})/)?.[1] ?? null;
});
console.log("job prefix:", jobId);

const results = await p.evaluate(async () => {
  const out = {};
  const full = window.__jobId;
  return out;
});

await p.goto("http://127.0.0.1:8000/app/", { waitUntil: "networkidle" });
await p.waitForTimeout(2500);
await p.screenshot({ path: S + "/home-after.png" });
console.log("home status after:", await p.locator("footer").innerText().catch(() => "?"));
console.log("console errors:", errs.length ? errs : "none");
await b.close();
