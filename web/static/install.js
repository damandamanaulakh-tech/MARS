/* AM07 install helper (owner asked 2026-07-26: "I didn't get it").
   Chrome buries "Install app" in a menu, so the app offers its own button.
   - Android / desktop Chrome+Edge: captures beforeinstallprompt, shows a
     floating button, fires the real native installer on tap.
   - iPhone Safari: no such event exists, so it shows the exact taps.
   - Already installed (standalone): shows nothing at all. */
(function () {
  var deferred = null;

  function installed() {
    return window.matchMedia("(display-mode: standalone)").matches ||
           window.navigator.standalone === true;
  }
  function isIOS() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
  }

  function button(label, onClick) {
    var b = document.createElement("button");
    b.id = "am07-install";
    b.textContent = label;
    b.setAttribute("aria-label", "Install AM07 as an app");
    b.style.cssText =
      "position:fixed;right:16px;bottom:16px;z-index:9999;" +
      "background:#047857;color:#fff;border:0;border-radius:999px;" +
      "padding:13px 20px;font:600 14px/1 system-ui,sans-serif;" +
      "box-shadow:0 6px 20px rgba(0,0,0,.28);cursor:pointer";
    b.onclick = onClick;
    document.body.appendChild(b);
    return b;
  }

  function iosSheet() {
    var d = document.createElement("div");
    d.style.cssText =
      "position:fixed;left:12px;right:12px;bottom:78px;z-index:9999;" +
      "background:#0b1220;color:#e8edf5;border-radius:14px;padding:16px 18px;" +
      "font:400 14px/1.5 system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.35)";
    d.innerHTML =
      "<b style='display:block;margin-bottom:6px'>Install AM07 on iPhone</b>" +
      "1. Tap the <b>Share</b> button (square with an arrow) at the bottom of Safari<br>" +
      "2. Scroll down, tap <b>Add to Home Screen</b><br>" +
      "3. Tap <b>Add</b> — the AM07 icon appears with your apps" +
      "<div style='margin-top:10px;text-align:right'><button id='am07-ios-x' " +
      "style='background:#1f2937;color:#e8edf5;border:0;border-radius:8px;padding:8px 14px;font:600 13px system-ui'>Got it</button></div>";
    document.body.appendChild(d);
    document.getElementById("am07-ios-x").onclick = function () { d.remove(); };
  }

  if (installed()) return;

  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferred = e;
    if (document.getElementById("am07-install")) return;
    button("📲 Install app", function () {
      if (!deferred) return;
      deferred.prompt();
      deferred.userChoice.then(function () {
        deferred = null;
        var el = document.getElementById("am07-install");
        if (el) el.remove();
      });
    });
  });

  window.addEventListener("appinstalled", function () {
    var el = document.getElementById("am07-install");
    if (el) el.remove();
  });

  if (isIOS()) {
    window.addEventListener("load", function () {
      button("📲 Install app", iosSheet);
    });
  }
})();
