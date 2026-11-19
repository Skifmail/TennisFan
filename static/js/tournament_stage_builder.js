const builder = document.querySelector('form[data-stage-builder]');
if (builder) {
    const kind = builder.elements.kind;
    const note = builder.querySelector('[data-selection]');
    const seedInputs = builder.querySelectorAll('[data-seed]');
    const descriptions = {
        custom_round: 'Каждый участник сыграет с каждым.',
        custom_knockout: 'Победитель проходит дальше. При неполной сетке первые номера посева получают проход без игры.',
        custom_places: 'Победители и проигравшие продолжают играть за свои места. Следующий круг появится после завершения текущего.'
    };
    const update = () => {
        const selected = builder.querySelectorAll('[name="members"]:checked').length;
        note.textContent = 'Выбрано: ' + selected;
        builder.querySelector('[data-format-hint]').textContent = descriptions[kind.value];
        for (const seed of seedInputs) {
            const selectedMember = seed.closest('.stage-builder__member').querySelector('[name="members"]').checked;
            seed.hidden = kind.value === 'custom_round' || !selectedMember;
        }
    };
    builder.addEventListener('change', update);
    update();
}

const placementForm = document.querySelector('[data-placement-form]');
const placementPreview = document.getElementById('placement-preview');
if (placementForm && placementPreview) {
    placementForm.addEventListener('input', () => { placementPreview.hidden = true; });
}
