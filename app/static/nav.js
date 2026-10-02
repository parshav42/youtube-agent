(() => {
  const installNavigation = () => {
    if (document.getElementById("motivationos-nav")) return;

    const style = document.createElement("style");
    style.textContent = `
      #motivationos-nav { position: fixed; inset: 0 0 auto; z-index: 1000; height: 56px; box-sizing: border-box; background: #0A1929; color: #fff; font: 500 15px/1.2 system-ui, sans-serif; }
      #motivationos-nav * { box-sizing: border-box; }
      #motivationos-nav .nav-inner { max-width: 1200px; height: 100%; margin: auto; padding: 0 24px; display: flex; align-items: center; justify-content: space-between; }
      #motivationos-nav a { color: inherit; text-decoration: none; }
      #motivationos-nav .nav-brand { font-size: 18px; font-weight: 750; letter-spacing: .01em; }
      #motivationos-nav .nav-links { height: 100%; display: flex; align-items: stretch; gap: 28px; }
      #motivationos-nav .nav-link { display: inline-flex; align-items: center; border-bottom: 3px solid transparent; padding-top: 3px; }
      #motivationos-nav .nav-link[aria-current="page"] { border-bottom-color: #FFD400; }
      #motivationos-nav .nav-toggle { display: none; color: #fff; background: none; border: 1px solid #496075; border-radius: 6px; padding: 7px 10px; font: inherit; cursor: pointer; }
      @media (max-width: 639px) {
        #motivationos-nav .nav-inner { padding: 0 16px; }
        #motivationos-nav .nav-toggle { display: block; }
        #motivationos-nav .nav-links { display: none; position: absolute; top: 56px; left: 0; right: 0; height: auto; padding: 8px 16px 14px; flex-direction: column; gap: 0; background: #0A1929; box-shadow: 0 8px 16px #0004; }
        #motivationos-nav .nav-links.is-open { display: flex; }
        #motivationos-nav .nav-link { min-height: 44px; border-bottom-width: 2px; }
      }
    `;
    document.head.appendChild(style);

    const path = window.location.pathname.replace(/\/$/, "") || "/";
    const links = [["/review", "Script Review"], ["/production-review", "Production Review"], ["/", "Home"]];
    const nav = document.createElement("nav");
    nav.id = "motivationos-nav";
    nav.setAttribute("aria-label", "Main navigation");
    const inner = document.createElement("div");
    inner.className = "nav-inner";
    const brand = document.createElement("a");
    brand.className = "nav-brand";
    brand.href = "/";
    brand.textContent = "MotivationOS";
    const toggle = document.createElement("button");
    toggle.className = "nav-toggle";
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Open navigation menu");
    toggle.textContent = "☰";
    const list = document.createElement("div");
    list.className = "nav-links";
    links.forEach(([href, label]) => {
      const link = document.createElement("a");
      link.className = "nav-link";
      link.href = href;
      link.textContent = label;
      if (href === path) link.setAttribute("aria-current", "page");
      list.appendChild(link);
    });
    toggle.addEventListener("click", () => {
      const open = list.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Close navigation menu" : "Open navigation menu");
    });
    inner.append(brand, toggle, list);
    nav.appendChild(inner);
    document.body.insertBefore(nav, document.body.firstChild);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", installNavigation, { once: true });
  } else {
    installNavigation();
  }
})();
