// Render the actual dashboard function with a minimal DOM; no live Hive access.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../share/dashboard.html'), 'utf8');
const start = html.indexOf('function renderMembers(');
const end = html.indexOf('\nfunction toggleRoomExpanded()', start);
assert(start >= 0 && end > start);
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {dataset: {}, innerHTML: '', textContent: ''});
  return elements.get(id);
};
const document = {querySelectorAll: () => []};
let currentTeam = '', memberTabChosen = true, currentMemberTab = 'inactive';
const esc = value => String(value), ago = () => 'now', updateRecipients = () => {};
eval(html.slice(start, end));

renderMembers([{member: 'astra-master', state: 'unknown', running: true}]);
let card = $('members').innerHTML;
assert(card.includes('data-terminal="astra-master"'));
assert(card.includes('data-action="restart"'));
assert(!card.includes('data-action="wake"'));
assert(card.includes('running · activity unknown'));

renderMembers([{member: 'astra-master', state: 'dead', running: false}]);
card = $('members').innerHTML;
assert(card.includes('data-action="wake"'));
assert(!card.includes('data-action="restart"'));
assert(!card.includes('data-terminal="astra-master"'));

currentMemberTab = 'idle';
renderMembers([{member: 'astra-master', state: 'idle'}]);
assert($('members').innerHTML.includes('data-terminal="astra-master"'));
console.log('Running unknown, stopped, and older-server member controls pass.');
