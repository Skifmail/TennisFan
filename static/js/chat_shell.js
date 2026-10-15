/* Общая оболочка чатов: мгновенная обратная связь и мобильный viewport. */
(function () {
    "use strict";
    document.addEventListener("pointerdown", () => { document.documentElement.dataset.inputMode = "pointer"; }, {passive: true});
    document.addEventListener("keydown", () => { document.documentElement.dataset.inputMode = "keyboard"; });
    const viewport = window.visualViewport;
    function fitViewport() {
        // Клавиатура iOS может менять visualViewport, не меняя CSS layout viewport.
        if (viewport && viewport.scale !== 1) return;
        const height = viewport ? viewport.height : window.innerHeight;
        document.documentElement.style.setProperty("--chat-viewport-height", `${height}px`);
    }
    fitViewport();
    window.addEventListener("resize", fitViewport, {passive: true});
    if (viewport) viewport.addEventListener("resize", fitViewport, {passive: true});
})();
