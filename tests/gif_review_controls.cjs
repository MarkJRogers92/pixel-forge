// Exercise the generated page's control logic without a browser dependency.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const payload = html.match(/<script type="application\/json" id="review-data">([\s\S]*?)<\/script>/)[1];
const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
const elements = new Map();
const get = id => {
  if (!elements.has(id)) elements.set(id, {value: '0', disabled: false});
  return elements.get(id);
};
get('review-data').textContent = payload;
const listeners = new Map();
const doc = {getElementById: get, body: {}, hidden: false,
  querySelectorAll: () => [get('current'), get('sheet-frame')],
  addEventListener: (name, fn) => listeners.set(name, fn)};
let nextId = 0;
const timers = new Map();
vm.runInNewContext(script, {document:doc,
  Image: class {set src(value) {this.onload();}},
  setTimeout: (fn, delay) => {timers.set(++nextId, {fn, delay}); return nextId;},
  clearTimeout: id => timers.delete(id)});
const tick = delay => {
  assert.equal(timers.size, 1, 'only one playback timer');
  const [id,timer] = timers.entries().next().value;
  assert.equal(timer.delay, delay); timers.delete(id); timer.fn();
};
// Two source frames at 80/230 ms, loop=2 means initial play plus two repeats.
get('play').onclick();
for (let repeat=0; repeat<3; repeat++) {
  assert.equal(get('current').src, 'frames/f0.png'); tick(80);
  assert.equal(get('current').src, 'frames/f1.png'); tick(230);
}
assert.equal(timers.size, 0);
assert.match(get('playback-status').textContent, /finished/);
get('play').onclick(); get('play').onclick(); assert.equal(timers.size, 1);
get('pause').onclick(); assert.equal(timers.size, 0);
get('prev').onclick(); assert.equal(get('current').src, 'frames/f1.png');
get('next').onclick(); assert.equal(get('current').src, 'frames/f0.png');
get('scrub').value='1'; get('scrub').oninput(); assert.equal(get('current').src, 'frames/f1.png');
get('scale').value='2'; get('scale').onchange();
assert.equal(get('current').width, 24); assert.equal(get('sheet-frame').height, 20);
get('background').value='dark'; get('background').onchange(); assert.equal(doc.body.className,'dark');
get('play').onclick(); doc.hidden=true; listeners.get('visibilitychange')(); assert.equal(timers.size,0);
console.log('finite repeats, delays, restart, pause, step, scrub, scale, background, hidden-page pause passed');
