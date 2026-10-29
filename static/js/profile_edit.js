/* Локальный предпросмотр фото: загрузка на сервер только при сохранении формы. */
(() => {
    const input = document.getElementById('id_avatar');
    const preview = document.getElementById('profile-avatar-preview');
    const status = document.getElementById('profile-avatar-status');
    if (!input || !preview || !status) return;
    const clear = document.getElementById('avatar-clear_id');
    const original = Array.from(preview.childNodes, node => node.cloneNode(true));
    let previewUrl = null;
    const release = () => {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = null;
    };
    const reset = () => preview.replaceChildren(...original.map(node => node.cloneNode(true)));
    const showSelection = () => {
        release();
        const file = input.files && input.files[0];
        if (!file) {
            reset();
            status.textContent = '';
            return;
        }
        if (clear) clear.checked = false;
        const url = URL.createObjectURL(file);
        previewUrl = url;
        const image = new Image();
        image.alt = '';
        image.className = 'profile-editor__avatar-image';
        image.onload = () => {
            if (previewUrl !== url) return;
            preview.replaceChildren(image);
            status.textContent = 'Новое фото появится в профиле после сохранения.';
        };
        image.onerror = () => {
            if (previewUrl !== url) return;
            reset();
            status.textContent = 'Не удалось показать изображение. Проверьте выбранный файл.';
        };
        image.src = url;
    };
    input.addEventListener('change', showSelection);
    if (clear) clear.addEventListener('change', () => {
        if (!clear.checked) {
            showSelection();
            return;
        }
        release();
        input.value = '';
        const placeholder = document.createElement('span');
        placeholder.className = 'profile-editor__initial';
        placeholder.setAttribute('aria-hidden', 'true');
        placeholder.textContent = 'Я';
        preview.replaceChildren(placeholder);
        status.textContent = 'Фото будет удалено после сохранения.';
    });
    window.addEventListener('pagehide', release);
})();
