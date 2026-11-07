// Actual reply/submit functions: resolution is explicit and lookup races are inert.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '../share/dashboard.html'), 'utf8');
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {value:'', hidden:false, checked:false,
    focus(){}, classList:{add(){},remove(){},toggle(){}}, textContent:'',innerHTML:''});
  return elements.get(id);
};
const lookups=[], posts=[];
let failPost = false, holdPost = null;
const fixtures = [{id:'first',gen:1,member:'alice',body:'First?',channel:'choice'},
                  {id:'second',gen:2,member:'bob',body:'Second?',channel:'combat'}];
const context=vm.createContext({crypto: require("node:crypto").webcrypto,$, encodeURIComponent, replyTo:null, currentChannel:'choice',
  channelEntries:fixtures, dashboardData:{room:[],beekeeper_mentions:[]},
  showTab(){}, memberName:v=>v, myBeekeeper:()=> 'beekeeper', esc:v=>String(v),
  mentionedNames:()=>new Set(['alice','bob']), insertMention(){}, hideMentionOptions(){},updateRecipients(){},render(){},
  fetch:(url,options)=> {
    if(url.startsWith('/api/messages')) return new Promise(resolve=>lookups.push({url,resolve}));
    if(url==='/api/room') {
      posts.push(JSON.parse(options.body));
      if (failPost) return Promise.reject(new Error('connection lost'));
      if (holdPost) return new Promise(resolve => { holdPost.resolve = resolve; });
    }
    return Promise.resolve({ok:true,json:async()=>({ok:true,sent:[],failed:{},unconfirmed:{}})});
  }});
const start=html.indexOf('function startReply('), end=html.indexOf('\ndocument.addEventListener("click"',start);
vm.runInContext(html.slice(start,end),context);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const respond=(lookup,id)=>lookup.resolve({ok:true,json:async()=>({id,kind:'request'})});
(async()=>{
  vm.runInContext('startReply(1); startReply(2)',context);
  respond(lookups[0],'first'); await settle();
  assert.equal($('reply-resolution').hidden,true);
  respond(lookups[1],'second'); await settle();
  assert.equal($('reply-resolution').hidden,false);
  assert.equal($('resolve-request').checked,false);
  $('room-message').value='Conversational answer';
  await vm.runInContext('sendRoomMessage()',context);
  assert.equal(posts[0].resolution,undefined);
  assert.equal(posts[0].reply_to,undefined);

  vm.runInContext('startReply(2)',context);
  respond(lookups[2],'second'); await settle();
  $('resolve-request').checked=true;
  $('room-message').value='This resolves the request';
  await vm.runInContext('sendRoomMessage()',context);
  assert.equal(posts[1].resolution,'ack');
  assert.equal(posts[1].reply_to,'second');
  assert.equal(posts[1].kind,'decision');
  assert.equal(posts[1].channel,'combat');
  assert.equal($('resolve-request').checked,false);

  vm.runInContext('startReply(1); cancelReply()',context);
  respond(lookups[3],'first'); await settle();
  assert.equal($('reply-resolution').hidden,true);
  $('room-message').value='Retry this unchanged draft';
  failPost = true;
  await vm.runInContext('sendRoomMessage()',context);
  const retryID = posts.at(-1).client_id;
  assert.match($('room-feedback').textContent, /may already be recorded/);
  await vm.runInContext('sendRoomMessage()',context);
  assert.equal(posts.at(-1).client_id, retryID);
  failPost = false;
  holdPost = {};
  const pending = vm.runInContext('sendRoomMessage()',context);
  const count = posts.length;
  await vm.runInContext('sendRoomMessage()',context);
  assert.equal(posts.length, count);
  holdPost.resolve({ok:true,json:async()=>({ok:true,sent:[],queued:['alice'],dispatch_pending:true})});
  await pending;
  assert.match($('room-feedback').textContent, /queued.*not yet confirmed/);
  assert.equal(posts.at(-1).client_id, retryID);
  console.log('Ordinary replies stay open; explicit resolution binds the request; stale lookups cannot enable resolution.');
})().catch(err=>{console.error(err);process.exitCode=1;});
