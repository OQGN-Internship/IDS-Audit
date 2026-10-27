// What a page draws, for tests/test_theme_browser.py (ENG-03 4).
() => {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const pen = canvas.getContext("2d", {willReadFrequently: true});
  const hex = value => {
    pen.clearRect(0, 0, 1, 1);
    pen.fillStyle = "#000000";
    pen.fillStyle = value;
    pen.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = pen.getImageData(0, 0, 1, 1).data;
    if (a === 0) return null;
    if (a < 255) return "translucent";
    return "#" + [r, g, b].map(c => c.toString(16).padStart(2, "0")).join("").toUpperCase();
  };
  const name = e => (e.tagName.toLowerCase() + (e.type === "radio" ? "[radio]" : "")
    + "." + [...e.classList].join(".")
    + " " + (e.textContent || "").trim().slice(0, 30));
  const shown = e => e.getClientRects().length > 0
    && getComputedStyle(e).visibility === "visible";
  const behind = e => {
    for (let at = e; at; at = at.parentElement) {
      const colour = hex(getComputedStyle(at).backgroundColor);
      if (colour) return colour;
    }
    return "#FFFFFF";
  };
  const ownText = e => [...e.childNodes].some(
    n => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
  const found = {colours: [], texts: [], corners: [], shadows: [], gradients: [], motion: []};
  const look = (style, where) => {
    const add = (value, what) => {
      const colour = hex(value);
      if (colour) found.colours.push([colour, what + " of " + where]);
    };
    add(style.backgroundColor, "background");
    for (const side of ["Top", "Right", "Bottom", "Left"]) {
      if (parseFloat(style["border" + side + "Width"]) > 0
          && style["border" + side + "Style"] !== "none") {
        add(style["border" + side + "Color"], "border");
      }
    }
    // A radio button stays round, as every radio button is (ENG-03 4).
    for (const corner of ["TopLeft", "TopRight", "BottomLeft", "BottomRight"]) {
      const radius = parseFloat(style["border" + corner + "Radius"]);
      if (radius !== 0 && !where.startsWith("input[radio]")) found.corners.push(where);
    }
    if (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0) {
      add(style.outlineColor, "outline");
    }
    if (style.boxShadow !== "none" || style.textShadow !== "none") found.shadows.push(where);
    if (style.backgroundImage.includes("gradient")) found.gradients.push(where);
    const moving = style.transitionDuration.split(",").some(d => parseFloat(d) > 0)
      || style.animationName !== "none";
    if (moving) found.motion.push(where);
  };
  for (const e of document.body.querySelectorAll("*")) {
    if (!shown(e) || e.closest("script, template, [data-chart]")) continue;
    const style = getComputedStyle(e);
    const where = name(e);
    look(style, where);
    const control = e.matches("input:not([type=hidden], [type=radio], [type=checkbox]), select, textarea, button");
    if (ownText(e) || control || e.matches("svg")) {
      const colour = hex(style.color);
      if (colour) {
        found.colours.push([colour, "text of " + where]);
        if (!e.matches("svg")) found.texts.push([colour, behind(e), where]);
      }
    }
    for (const part of ["::before", "::after"]) {
      const drawn = getComputedStyle(e, part);
      if (drawn.content && !["none", "normal"].includes(drawn.content)) {
        look(drawn, where + " " + part);
        if (drawn.content !== '""') {
          const colour = hex(drawn.color);
          if (colour) {
            found.colours.push([colour, "text of " + where + " " + part]);
            found.texts.push([colour, behind(e), where + " " + part]);
          }
        }
      }
    }
    if (e.matches("input[type=file]")) {
      const button = getComputedStyle(e, "::file-selector-button");
      look(button, where + " ::file-selector-button");
      const colour = hex(button.color);
      if (colour) found.texts.push([colour, hex(button.backgroundColor) || behind(e), where + " file button"]);
    }
  }
  return found;
}
