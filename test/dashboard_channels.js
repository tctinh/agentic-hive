// Exercise actual channel UI functions and response races without a live Hive.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '../share/dashboard.html'), 'utf8');
const script = html.slice(html.indexOf('<script>') + 8, html.lastIndexOf('</script>'));
new vm.Script(script); // Compile the entire shipped dashboard, not a rewritten fixture.
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {value: '', dataset: {}, hidden: false,
    textContent: '', innerHTML: '', classList: {add(){}, remove(){}}});
  return elements.get(id);
};
const requests = [];
const renders = [];
const context = vm.createContext({$, URLSearchParams, encodeURIComponent,
  currentChannel: '', channelQuery: '', channelEntries: [], channelBefore: null,
  channelRequest: 0, channelGeneration: null, channelViewingOlder: false,
  renderedRoomKey: null, dashboardData: {generation: 7, members: []},
  fetch: url => new Promise(resolve => requests.push({url, resolve})),
  render: () => {}, renderRoom: entries => renders.push(entries),
  esc: v => String(v).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;')});
const start = script.indexOf('function scopedRoom(');
const end = script.indexOf('function renderRoom(', start);
vm.runInContext(script.slice(start, end), context);
const settle = () => new Promise(resolve => setImmediate(resolve));
const respond = (request, data) => request.resolve({ok: true, json: async () => data});

(async () => {
  $('room-message').value = 'Unsent work stays here';
  vm.runInContext('chooseChannel("combat")', context);
  assert(requests[0].url.includes('channel=combat'));
  vm.runInContext('chooseChannel("terrain")', context);
  respond(requests[1], {messages: [{id:'new', sequence:8, sender:'bob', body:'terrain', channel:'terrain'}], next_before:8});
  await settle();
  respond(requests[0], {messages: [{id:'stale', sequence:1, sender:'alice', body:'wrong channel'}]});
  await settle();
  assert.equal(context.channelEntries[0].id, 'new');
  assert.equal($('room-message').value, 'Unsent work stays here');
  assert.equal($('channel-older').hidden, false);

  vm.runInContext('loadChannel(true)', context);
  assert(requests[2].url.includes('before=8'));
  respond(requests[2], {messages:[{id:'older', sequence:3, sender:'alice', body:'old'}], next_before:null});
  await settle();
  assert.equal(context.channelEntries[0].id, 'older');
  assert.equal($('channel-latest').hidden, false);
  vm.runInContext('renderChannels({generation:9, channels:{terrain:{purpose:"<unsafe>",lead:"bob"}}})', context);
  assert.equal(requests.length, 3); // New traffic does not steal an older page.
  assert($('room-channel').innerHTML.includes('terrain'));
  assert($('channel-purpose').textContent.includes('<unsafe>')); // textContent, not HTML.

  $('channel-search').value = 'chase speed';
  vm.runInContext('searchChannels()', context);
  assert(requests[3].url.includes('q=chase+speed'));
  respond(requests[3], {messages:[], next_before:null});
  await settle();
  assert.equal(context.channelViewingOlder, false);
  assert.equal($('channel-older').hidden, true);

  assert(html.includes('data-full-message='));
  assert(html.includes('payload.kind = $("room-kind").value'));
  assert(html.includes('channel: replyTo?.channel || currentChannel || "hive"'));
  console.log('Channel selection, search, pagination, response races, draft preservation and dashboard syntax pass.');
})().catch(err => { console.error(err); process.exitCode = 1; });
