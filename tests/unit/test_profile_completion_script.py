"""Проверка периодичности напоминания и изоляции между пользователями."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase


@skipUnless(shutil.which("node"), "Node.js не установлен")
class ProfileCompletionScriptTests(SimpleTestCase):
    """Окно не повторяется на каждой странице и возвращается после отсрочки."""

    def test_reminder_interval_and_per_user_storage(self) -> None:
        script = Path(settings.BASE_DIR, "static/js/profile_completion.js").read_text()
        harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = process.argv[1];
const storage = new Map();
let now = 100000000;
function page(user) {
    const timers = [];
    let closed;
    const dialog = {open:false, shows:0, showModal(){this.open=true;this.shows++;}, close(){this.open=false;}};
    const context = {
        Date: {now:()=>now}, JSON, Number,
        localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
        setTimeout:(fn,delay)=>{assert.equal(delay,15000);timers.push(fn);},
        setInterval:fn=>timers.push(fn),
        document:{visibilityState:'visible',querySelector:()=>null,getElementById:id=>{
            if(id==='profile-completion-data') return {textContent:JSON.stringify({user_id:user,interval_ms:21600000})};
            if(id==='profile-completion-dialog') return dialog;
            return {addEventListener:(_event,fn)=>{closed=fn;}};
        }}
    };
    vm.runInNewContext(code,context);
    timers[0]();
    return {dialog,timers,close:()=>closed()};
}
const first=page(1); assert.equal(first.dialog.shows,1);first.close();
first.timers[1]();assert.equal(first.dialog.shows,1);
const next=page(1);assert.equal(next.dialog.shows,0);
assert.equal(page(2).dialog.shows,1);
now+=21600000;next.timers[1]();assert.equal(next.dialog.shows,1);
"""
        result = subprocess.run(
            [shutil.which("node") or "node", "-e", harness, script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
