const express = require('express');
const cors = require('cors');
const fs = require('fs');
const path = require('path');
const QRCode = require('qrcode');
const pino = require('pino');
const { spawn } = require('child_process');
let ffmpegPath = null;
let mime = null;
try { ffmpegPath = require('ffmpeg-static'); } catch (_) {}
try { mime = require('mime-types'); } catch (_) {}
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  Browsers,
  downloadContentFromMessage,
} = require('@whiskeysockets/baileys');

const PORT = Number(process.env.MARKXLVIII_WA_PORT || process.env.CYBERBOT_WA_PORT || 8765);
const ROOT = __dirname;
const AUTH_DIR = path.join(ROOT, 'auth');
const QR_FILE = path.join(ROOT, 'wa_qr.png');
const STATUS_FILE = path.join(ROOT, 'status.json');
const LOG_FILE = path.join(ROOT, 'bridge.log');
const DB_FILE = path.join(ROOT, 'mark_xlviii_whatsapp_db.json');
const MEDIA_DIR = path.join(ROOT, 'media');
const VERSION = '2026-09-23-uma-conexao-38';
const MAX_SEND_BYTES = Number(process.env.MARKXLVIII_WHATSAPP_MAX_FILE_MB || process.env.CYBERBOT_WHATSAPP_MAX_FILE_MB || 100) * 1024 * 1024;
try { fs.mkdirSync(MEDIA_DIR, { recursive: true }); } catch (_) {}

let sock = null;
let connected = false;
let lastQr = '';
let lastEvent = 'starting';
let startedAt = new Date().toISOString();
let contacts = {};
let chats = {};
let groups = {};
let aliases = {};
let messages = [];
let starting = false;
let qrWatchdog = null;
// UMA CONEXAO SO (23/09/2026, "estou conectado e o JARVIS diz que nao").
//
// O bridge.log de 22/09 mostrou DUAS conexoes do Baileys vivas no mesmo
// processo: o botao "GERAR QR NOVO" (/reset) encerrava a conexao atual e
// agendava uma nova — e o evento de fechamento DESSA MESMA conexao agendava
// outra ("reconnect"), 3 s depois. Uma conectava de verdade; a outra ficava
// gerando QR Code a cada minuto e escrevendo `connected:false, has_qr:true` por
// cima. O JARVIS lia isso e respondia "o WhatsApp ainda nao esta conectado,
// escaneie o QR". Depois as duas se derrubavam em laco (codigo 440: "outra
// sessao substituiu esta"), e a cada queda nascia mais uma.
//
// Agora cada conexao tem um NUMERO (`geracao`). So a conexao da geracao atual
// pode mudar o estado, gravar credencial e pedir reconexao; a antiga e
// encerrada antes de a nova nascer, e o que ela disser depois disso e ignorado.
// E so existe UMA reconexao agendada por vez.
let geracao = 0;
let reconexao = null;
function cancelarReconexao() { if (reconexao) clearTimeout(reconexao); reconexao = null; }
function encerrarConexaoAtual() {
  geracao += 1;                 // o que a conexao velha disser daqui em diante nao vale
  cancelarReconexao();
  const velho = sock;
  sock = null;
  if (velho) { try { if (velho.end) velho.end(undefined); } catch (_) {} }
}
function agendarReconexao(minha, espera) {
  if (minha !== geracao || reconexao) return;
  reconexao = setTimeout(() => {
    reconexao = null;
    if (minha !== geracao) return;
    start({ fresh: false }).catch(err => log('Reconnect failed', err?.stack || err));
  }, espera);
}
let lastFailedName = '';
let pendingLeaveGroup = null;
let recentDeleteBatch = { signature: '', at: 0, response: null };
let lastDisconnectReason = '';
let watchConfig = { enabled: false, mode: 'off', contacts: [], include_groups: true, target_type: 'all', updated_at: '' };
let alertQueue = [];
let alertSeen = {};
let agentQueue = [];
let agentSeen = {};
let mediaDownloadActive = new Set();
let freshLoginRequired = false;
let manualLogoutInProgress = false;
let contactSyncInFlight = false;



function log(...args) {
  const line = `[${new Date().toISOString()}] ` + args.map(a => {
    try { return typeof a === 'string' ? a : JSON.stringify(a); } catch (_) { return String(a); }
  }).join(' ');
  console.log(line);
  try { fs.appendFileSync(LOG_FILE, line + '\n'); } catch (_) {}
}
function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_resolve, reject) => setTimeout(() => reject(new Error(`${label} timeout after ${ms}ms`)), ms))
  ]);
}
function loadDb() {
  try {
    if (fs.existsSync(DB_FILE)) {
      const d = JSON.parse(fs.readFileSync(DB_FILE, 'utf8') || '{}');
      contacts = d.contacts || {};
      chats = d.chats || {};
      groups = d.groups || {};
      aliases = d.aliases || {};
      messages = Array.isArray(d.messages) ? d.messages : [];
      // LIMPEZA DOS REGISTROS VAZIOS (23/09/2026). O banco do Diego tinha 3.000
      // mensagens e 2.958 eram "[unknown]": avisos sem conteudo (mensagem que o
      // WhatsApp nao deixou decifrar, sincronizacao de chave) que entraram em
      // rajada de madrugada e empurraram as conversas de verdade para fora do
      // teto de 3.000. "Ve minhas ultimas mensagens" nao achava o privado.
      const antes = messages.length;
      messages = messages.filter(x => x && !REGISTRO_VAZIO.has(String(x.type || '')) && x.text !== '[unknown]');
      if (messages.length !== antes) console.log(`[db] ${antes - messages.length} registros vazios removidos`);
      watchConfig = d.watchConfig || watchConfig;
      alertQueue = Array.isArray(d.alertQueue) ? d.alertQueue : [];
      alertSeen = d.alertSeen || {};
      agentQueue = Array.isArray(d.agentQueue) ? d.agentQueue : [];
      agentSeen = d.agentSeen || {};
      pendingLeaveGroup = d.pendingLeaveGroup || pendingLeaveGroup;
    }
  } catch (e) { log('loadDb error', e.message); }
}
function saveDbAgora() {
  if (saveDbTimer) { clearTimeout(saveDbTimer); saveDbTimer = null; }
  try {
    if (messages.length > 3000) messages = messages.slice(-3000);
    fs.writeFileSync(DB_FILE, JSON.stringify({ contacts, chats, groups, aliases, messages, watchConfig, alertQueue, alertSeen, agentQueue, agentSeen, pendingLeaveGroup, updated_at: new Date().toISOString() }, null, 2));
  } catch (e) { log('saveDb error', e.message); }
}
// GRAVAR O BANCO NO MAXIMO A CADA 1,5 s (23/09/2026). Era a cada mensagem,
// contato e grupo: um arquivo de ~3 MB escrito de uma vez, com o Node parado
// esperando. Numa sincronizacao chegam centenas de eventos por minuto, a ponte
// deixava de atender a porta e o JARVIS entendia "WhatsApp desconectado". Os
// dados continuam na memoria na hora; so o arquivo e que espera 1,5 s.
let saveDbTimer = null;
function saveDb() {
  if (saveDbTimer) return;
  saveDbTimer = setTimeout(() => { saveDbTimer = null; saveDbAgora(); }, 1500);
}
for (const sinal of ['SIGINT', 'SIGTERM', 'SIGBREAK']) {
  try { process.on(sinal, () => { saveDbAgora(); process.exit(0); }); } catch (_) {}
}
// Aviso de protocolo, sem nada para ler: nao entra no banco. Ver loadDb.
const REGISTRO_VAZIO = new Set(['unknown', 'senderKeyDistributionMessage', 'messageContextInfo']);
loadDb();
function currentConnected() { return Boolean(connected && sock && sock.user); }
function contactCount() { return Object.keys(contacts || {}).length; }
function chatCount() { return Object.keys(chats || {}).length; }
function groupCount() { return Object.keys(groups || {}).length; }
function msgCount() { return Array.isArray(messages) ? messages.length : 0; }
function lastMessage() { return messages.length ? messages[messages.length - 1] : null; }
function saveStatus(extra = {}) {
  const isConnected = currentConnected();
  const data = {
    ok: true,
    bridge: 'mark-xlviii-whatsapp-baileys',
    version: VERSION,
    bridge_root: ROOT,
    connected: isConnected,
    has_qr: Boolean(lastQr && !isConnected && fs.existsSync(QR_FILE)),
    qr_file: QR_FILE,
    status_file: STATUS_FILE,
    log_file: LOG_FILE,
    db_file: DB_FILE,
    port: PORT,
    contacts: contactCount(),
    chats: chatCount(),
    groups: groupCount(),
    messages: msgCount(),
    safe_sync: true,
    full_history_forced: false,
    note: 'Modo seguro: lê novas mensagens e qualquer histórico que o WhatsApp liberar sem derrubar a conexão.',
    last_message: lastMessage(),
    alert_watch: watchConfig,
    alert_queue: alertQueue.length,
    agent_queue: agentQueue.length,
    pending_leave_group: pendingLeaveGroup,
    disconnect_reason: lastDisconnectReason,
    fresh_login_required: Boolean(freshLoginRequired),
    last_event: lastEvent,
    started_at: startedAt,
    updated_at: new Date().toISOString(),
    ...extra,
  };
  if (extra.connected !== undefined) data.connected = extra.connected;
  try { fs.writeFileSync(STATUS_FILE, JSON.stringify(data, null, 2)); } catch (_) {}
  return data;
}
function statusPayload(extra = {}) { return saveStatus(extra); }
function digitsOnly(text) { return String(text || '').replace(/\D/g, ''); }
function normalizeBrazilNumber(n) { let d = digitsOnly(n); if (!d) return ''; if (d.length === 10 || d.length === 11) d = '55' + d; return d; }
function foldFancyChars(text) {
  // Converte letras matemáticas/negrito/itálico usadas em nomes de grupos para ASCII.
  // Ex.: 𝙂𝙤𝙢𝙮𝙯 𝙎𝙩𝙤𝙧𝙚 -> Gomyz Store.
  const ranges = [
    [0x1D400, 'A'.charCodeAt(0), 26], [0x1D41A, 'a'.charCodeAt(0), 26],
    [0x1D434, 'A'.charCodeAt(0), 26], [0x1D44E, 'a'.charCodeAt(0), 26],
    [0x1D468, 'A'.charCodeAt(0), 26], [0x1D482, 'a'.charCodeAt(0), 26],
    [0x1D49C, 'A'.charCodeAt(0), 26], [0x1D4B6, 'a'.charCodeAt(0), 26],
    [0x1D4D0, 'A'.charCodeAt(0), 26], [0x1D4EA, 'a'.charCodeAt(0), 26],
    [0x1D504, 'A'.charCodeAt(0), 26], [0x1D51E, 'a'.charCodeAt(0), 26],
    [0x1D538, 'A'.charCodeAt(0), 26], [0x1D552, 'a'.charCodeAt(0), 26],
    [0x1D56C, 'A'.charCodeAt(0), 26], [0x1D586, 'a'.charCodeAt(0), 26],
    [0x1D5A0, 'A'.charCodeAt(0), 26], [0x1D5BA, 'a'.charCodeAt(0), 26],
    [0x1D5D4, 'A'.charCodeAt(0), 26], [0x1D5EE, 'a'.charCodeAt(0), 26],
    [0x1D608, 'A'.charCodeAt(0), 26], [0x1D622, 'a'.charCodeAt(0), 26],
    [0x1D63C, 'A'.charCodeAt(0), 26], [0x1D656, 'a'.charCodeAt(0), 26],
    [0x1D670, 'A'.charCodeAt(0), 26], [0x1D68A, 'a'.charCodeAt(0), 26],
    [0x1D7CE, '0'.charCodeAt(0), 10], [0x1D7D8, '0'.charCodeAt(0), 10],
    [0x1D7E2, '0'.charCodeAt(0), 10], [0x1D7EC, '0'.charCodeAt(0), 10],
    [0x1D7F6, '0'.charCodeAt(0), 10],
  ];
  let out = '';
  for (const ch of String(text || '')) {
    const cp = ch.codePointAt(0);
    let mapped = '';
    for (const [start, base, len] of ranges) {
      if (cp >= start && cp < start + len) { mapped = String.fromCharCode(base + (cp - start)); break; }
    }
    out += mapped || ch;
  }
  return out;
}
function normalizeText(t) { return foldFancyChars(String(t || '')).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(); }
function compactText(t) { return normalizeText(t).replace(/\s+/g, ''); }
function editDistance(a, b) {
  a = String(a || ''); b = String(b || '');
  if (a === b) return 0;
  if (!a) return b.length; if (!b) return a.length;
  const prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  const cur = new Array(b.length + 1);
  for (let i = 1; i <= a.length; i++) {
    cur[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
    }
    for (let j = 0; j <= b.length; j++) prev[j] = cur[j];
  }
  return prev[b.length];
}
function similarity(a, b) {
  a = normalizeText(a); b = normalizeText(b);
  if (!a || !b) return 0;
  const maxLen = Math.max(a.length, b.length);
  return maxLen ? (1 - editDistance(a, b) / maxLen) : 0;
}
function fuzzyNameScore(name, query) {
  const norm = normalizeText(name);
  const target = normalizeText(query);
  const cn = compactText(name);
  const ct = compactText(query);
  if (!target) return 1;
  if (norm === target || cn === ct) return 140;
  if (norm.startsWith(target) || cn.startsWith(ct)) return 120;
  if (norm.includes(target) || cn.includes(ct)) return 105;
  if (target.includes(norm) && norm.length >= 4) return 90;
  const simFull = similarity(norm, target);
  let score = simFull >= 0.72 ? Math.round(75 + simFull * 25) : 0;
  const nameTokens = norm.split(/\s+/).filter(Boolean);
  const targetTokens = target.split(/\s+/).filter(Boolean);
  let tokenScore = 0;
  for (const t of targetTokens) {
    if (t.length < 2) continue;
    let best = 0;
    for (const n of nameTokens) {
      if (n === t) best = Math.max(best, 30);
      else if (n.includes(t) || t.includes(n)) best = Math.max(best, 22);
      else {
        const st = similarity(n, t);
        if (st >= 0.62) best = Math.max(best, Math.round(18 + st * 15));
      }
    }
    tokenScore += best;
  }
  // Número citado na fala (#300, 13 etc.) aumenta confiança.
  const numsQ = target.match(/\d+/g) || [];
  for (const num of numsQ) if (norm.includes(num)) tokenScore += 25;
  score = Math.max(score, Math.min(115, tokenScore));
  return score;
}
function aliasKey(name) { return normalizeText(name); }
function isGroupJid(jid) { return String(jid || '').endsWith('@g.us'); }
function isPersonJid(jid) { return String(jid || '').endsWith('@s.whatsapp.net') || String(jid || '').endsWith('@lid'); }
function linkedPersonJids(jid) {
  const ids = new Set([String(jid || '')].filter(Boolean));
  for (let pass = 0; pass < 2; pass++) {
    for (const [key, c] of Object.entries(contacts || {})) {
      const candidates = [key, c?.id, c?.jid, c?.lid].map(v => String(v || '')).filter(isPersonJid);
      if (candidates.some(v => ids.has(v))) for (const value of candidates) ids.add(value);
    }
  }
  return ids;
}
function filterMessagesByResolvedChat(list, jid) {
  const target = String(jid || '');
  if (!target) return list;
  if (isGroupJid(target)) return list.filter(m => String(m?.jid || '') === target);
  const personIds = linkedPersonJids(target);
  return list.filter(m => {
    if (m?.is_group || isGroupJid(m?.jid)) return false;
    return personIds.has(String(m?.jid || '')) || personIds.has(String(m?.sender_jid || ''));
  });
}
function aliasPriority(source) {
  const priorities = { manual: 100, address_book: 80, private_chat: 50, private_message: 40, private_history: 30, legacy_private: 20, message_or_chat_name: 10 };
  return priorities[String(source || '')] || 0;
}
function saveAlias(name, jidOrPhone) {
  const key = aliasKey(name);
  if (!key) throw new Error('Alias name is empty');
  let jid = String(jidOrPhone || '').trim();
  if (!jid.includes('@')) {
    const num = normalizeBrazilNumber(jid);
    if (!num || num.length < 11) throw new Error('Phone number is invalid');
    jid = `${num}@s.whatsapp.net`;
  }
  aliases[key] = { name: String(name || '').trim(), jid, manual: true, source: 'manual', updated_at: new Date().toISOString() };
  rememberContact(jid, { notify: String(name || '').trim() || jid }, { source: 'manual_alias' });
  saveDb();
  saveStatus({ last_event: 'alias', message: `Contato salvo: ${name} -> ${jid}` });
  return aliases[key];
}

function autoAliasFromName(name, jid, source = 'private_message') {
  try {
    const cleanName = String(name || '').trim();
    const cleanJid = String(jid || '').trim();
    if (!cleanName || !cleanJid || cleanName === 'Você' || cleanName === 'Contato') return;
    if (cleanName.includes('@') || cleanName.length < 2) return;
    // Nome de grupo e nome de participante visto apenas dentro de grupo nÃ£o podem
    // virar automaticamente o destino de uma mensagem privada.
    if (!isPersonJid(cleanJid)) return;
    const key = aliasKey(cleanName);
    if (!key) return;
    const current = aliases[key];
    if (current && current.auto !== true) return;
    if (current && aliasPriority(current.source) > aliasPriority(source)) return;
    if (current && current.jid !== cleanJid && aliasPriority(current.source) === aliasPriority(source)) return;
    aliases[key] = { name: cleanName, jid: cleanJid, auto: true, source, updated_at: new Date().toISOString() };
  } catch (e) { log('autoAliasFromName error', e.message); }
}
function aliasFromMessageHistory(name) {
  const target = aliasKey(name);
  if (!target) return null;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] || {};
    if (m.is_group || isGroupJid(m.jid)) continue;
    const candidates = [
      { name: m.name, jid: m.sender_jid || m.jid },
      { name: m.chat_name, jid: m.jid },
    ];
    for (const c of candidates) {
      if (!c.name || !c.jid) continue;
      const key = aliasKey(c.name);
      if (key === target || key.includes(target) || target.includes(key)) {
        autoAliasFromName(c.name, c.jid, 'private_history');
        saveDb();
        return aliases[aliasKey(c.name)] || { name: c.name, jid: c.jid, auto: true };
      }
    }
  }
  return null;
}
function displayName(obj, fallback = '') { return obj?.name || obj?.notify || obj?.verifiedName || obj?.subject || obj?.shortName || obj?.pushName || fallback; }
function friendlyNameForJid(jid) { return displayName(contacts[jid] || chats[jid] || groups[jid] || {}, jid); }
function shortJid(jid) {
  const raw = String(jid || '').split('@')[0] || '';
  if (!raw) return '';
  if (raw.length <= 10) return raw;
  return raw.slice(-8);
}
function _looksLikeRawParticipantName(name, senderJid = '') {
  const raw = String(name || '').trim();
  if (!raw) return true;
  if (raw === senderJid) return true;
  if (raw.includes('@s.whatsapp.net') || raw.includes('@lid') || raw.includes('@g.us')) return true;
  if (/^participante\s+\d{3,}$/i.test(raw)) return true;
  if (/^\d{8,}$/.test(raw.replace(/\D/g, ''))) return true;
  return false;
}
function _senderNameFromKnownMaps(senderJid) {
  const contact = contacts[senderJid] || chats[senderJid] || aliases[aliasKey(senderJid)] || {};
  const vals = [contact.name, contact.notify, contact.verifiedName, contact.shortName, contact.pushName, friendlyNameForJid(senderJid)];
  for (const v of vals) {
    const name = String(v || '').trim();
    if (!_looksLikeRawParticipantName(name, senderJid)) return name;
  }
  return '';
}
function chooseGroupSenderName(msg, senderJid, chatName) {
  const groupNorm = normalizeText(chatName || '');
  const candidates = [];
  const savedName = contacts[senderJid]?.name_source === 'address_book' ? contacts[senderJid]?.name : '';
  if (savedName) candidates.push(savedName);
  if (msg?.pushName) candidates.push(msg.pushName);
  const known = _senderNameFromKnownMaps(senderJid);
  if (known) candidates.push(known);
  for (const c of candidates) {
    const name = String(c || '').trim();
    if (!name) continue;
    const norm = normalizeText(name);
    if (!norm || norm === groupNorm) continue;
    if (name.endsWith('@g.us')) continue;
    if (_looksLikeRawParticipantName(name, senderJid)) continue;
    return name;
  }
  // Não inventar nome nem falar código/ID. Se o WhatsApp não liberar o nome do participante,
  // o Jarvis fala apenas "participante do grupo".
  return 'participante do grupo';
}
function rememberContact(jid, data = {}, options = {}) {
  if (!jid || jid === 'status@broadcast' || isGroupJid(jid)) return;
  const source = String(options.source || 'unknown');
  const incoming = { ...(data || {}) };
  const visibleName = String(incoming.name || '').trim();
  if (source === 'message' || source === 'manual_alias') {
    // pushName/notify pertence ao perfil do WhatsApp. Nunca deve substituir o
    // nome que chegou da agenda do usuÃ¡rio.
    delete incoming.name;
  }
  const merged = { ...(contacts[jid] || {}), ...incoming, id: jid, updated_at: new Date().toISOString() };
  if (source === 'address_book' && visibleName) {
    merged.name = visibleName;
    merged.name_source = 'address_book';
    autoAliasFromName(visibleName, jid, 'address_book');
    if (chats[jid] && !isGroupJid(jid)) chats[jid] = { ...chats[jid], name: visibleName, id: jid, updated_at: new Date().toISOString() };
    for (const item of messages) {
      if (!item?.from_me && item?.sender_jid === jid) item.name = visibleName;
      if (!item?.is_group && item?.jid === jid) item.chat_name = visibleName;
    }
    for (const queue of [alertQueue, agentQueue]) {
      for (const item of queue) {
        if (item?.sender_jid === jid) item.name = visibleName;
        if (!item?.is_group && item?.jid === jid) item.chat_name = visibleName;
      }
    }
  }
  contacts[jid] = merged;
}
function rememberChat(jid, data = {}) { if (!jid || jid === 'status@broadcast') return; chats[jid] = { ...(chats[jid] || {}), ...data, id: jid, updated_at: new Date().toISOString() }; }
function isUsableGroupName(name, jid = '') {
  const clean = String(name || '').trim();
  if (!clean || clean === String(jid || '').trim() || isGroupJid(clean)) return false;
  return normalizeText(clean) !== 'participante do grupo';
}
function rememberGroup(jid, data = {}) {
  if (!jid || !jid.endsWith('@g.us')) return;
  const incoming = { ...(data || {}) };
  const incomingName = incoming.subject || incoming.name || '';
  if (!isUsableGroupName(incomingName, jid)) {
    delete incoming.name;
    delete incoming.subject;
  }
  const merged = { ...(groups[jid] || {}), ...incoming, id: jid, updated_at: new Date().toISOString() };
  const resolvedName = merged.subject || merged.name || '';
  groups[jid] = merged;
  rememberChat(jid, isUsableGroupName(resolvedName, jid) ? { ...incoming, name: resolvedName, subject: resolvedName } : incoming);
  if (isUsableGroupName(resolvedName, jid)) {
    for (const item of messages) if (item?.jid === jid) item.chat_name = resolvedName;
    for (const queue of [alertQueue, agentQueue]) for (const item of queue) if (item?.jid === jid) item.chat_name = resolvedName;
  }
}
function unwrapMessage(message) {
  let m = message || {};
  for (let i = 0; i < 8; i++) {
    if (m.ephemeralMessage?.message) { m = m.ephemeralMessage.message; continue; }
    if (m.viewOnceMessage?.message) { m = m.viewOnceMessage.message; continue; }
    if (m.viewOnceMessageV2?.message) { m = m.viewOnceMessageV2.message; continue; }
    if (m.viewOnceMessageV2Extension?.message) { m = m.viewOnceMessageV2Extension.message; continue; }
    if (m.documentWithCaptionMessage?.message) { m = m.documentWithCaptionMessage.message; continue; }
    if (m.editedMessage?.message) { m = m.editedMessage.message; continue; }
    break;
  }
  return m || {};
}

function textCandidate(v) {
  if (v === undefined || v === null) return '';
  let s = String(v).replace(/\s+/g, ' ').trim();
  if (!s) return '';
  if (/^[A-Za-z0-9+/=]{80,}$/.test(s)) return '';
  if (/^[0-9]{8,}$/.test(s)) return '';
  if (/^https?:\/\//i.test(s) && s.length > 80) return '';
  if (/^(true|false|null|undefined)$/i.test(s)) return '';
  return s;
}
function collectTextDeep(obj, depth = 0, out = []) {
  if (!obj || depth > 8 || out.length > 24) return out;
  if (typeof obj === 'string' || typeof obj === 'number') {
    const c = textCandidate(obj);
    if (c) out.push(c);
    return out;
  }
  if (Array.isArray(obj)) {
    for (const x of obj) collectTextDeep(x, depth + 1, out);
    return out;
  }
  if (typeof obj !== 'object') return out;
  const preferred = [
    'conversation','text','caption','title','subtitle','description','selectedDisplayText',
    'selectedRowId','displayText','buttonText','name','body','footer','label','contentText',
    'matchedText','canonicalUrl','fileName','mimetype','paramsJson','buttonParamsJson'
  ];
  for (const key of preferred) {
    if (!(key in obj)) continue;
    let val = obj[key];
    if ((key === 'paramsJson' || key === 'buttonParamsJson') && typeof val === 'string') {
      try { val = JSON.parse(val); } catch (_) {}
    }
    if (typeof val === 'object') collectTextDeep(val, depth + 1, out);
    else {
      const c = textCandidate(val);
      if (c) out.push(c);
    }
  }
  for (const key of Object.keys(obj)) {
    if (preferred.includes(key)) continue;
    if (/messageContextInfo|jpegThumbnail|thumbnail|mediaKey|directPath|urlTrackingMap/i.test(key)) continue;
    if (/Message$|Reply|Response|interactive|nativeFlow|button|list|poll|header|body|footer|context/i.test(key)) {
      collectTextDeep(obj[key], depth + 1, out);
    }
  }
  return out;
}
function uniqueJoin(parts, textoDireto = false) {
  const seen = new Set();
  const out = [];
  for (const p of parts) {
    // TEXTO DIGITADO PELA PESSOA VALE COMO VEIO (23/09/2026). O filtro de
    // `textCandidate` existe para o lixo das mensagens interativas (ids,
    // base64, link de rastreio). Aplicado ao texto de verdade ele jogava fora
    // mensagem que e so um LINK comprido — e o JARVIS lia "[extendedTextMessage]"
    // em vez do link que o Diego mandou.
    const c = textoDireto ? String(p == null ? '' : p).replace(/\s+/g, ' ').trim() : textCandidate(p);
    if (!c) continue;
    const k = normalizeText(c);
    if (!k || seen.has(k)) continue;
    if (/^(interactive message|native flow|unknown|protocol message)$/i.test(c)) continue;
    seen.add(k);
    out.push(c);
  }
  return out.join(' | ').slice(0, 900);
}
function getMessageText(msg) {
  const m = unwrapMessage(msg.message || {});
  if (m.deviceSentMessage?.message) return getMessageText({ message: m.deviceSentMessage.message });
  if (m.protocolMessage?.editedMessage) return getMessageText({ message: m.protocolMessage.editedMessage });
  const direct = uniqueJoin([
    m.conversation,
    m.extendedTextMessage?.text,
    m.imageMessage?.caption,
    m.videoMessage?.caption,
    m.documentMessage?.caption,
    m.buttonsResponseMessage?.selectedDisplayText,
    m.listResponseMessage?.singleSelectReply?.selectedRowId,
    m.listResponseMessage?.title,
    m.templateButtonReplyMessage?.selectedDisplayText,
    m.pollCreationMessage?.name,
    m.contactMessage?.displayName,
    m.locationMessage?.name,
    m.liveLocationMessage?.caption,
    m.reactionMessage?.text,
  ], true);
  if (direct) return direct;
  const deep = uniqueJoin([
    ...collectTextDeep(m.interactiveMessage || {}),
    ...collectTextDeep(m.interactiveResponseMessage || {}),
    ...collectTextDeep(m.buttonsMessage || {}),
    ...collectTextDeep(m.templateMessage || {}),
    ...collectTextDeep(m.listMessage || {}),
    ...collectTextDeep(m.productMessage || {}),
    ...collectTextDeep(m.orderMessage || {}),
    ...collectTextDeep(m.pollCreationMessage || {}),
  ]);
  if (deep) return deep;
  return '';
}
function messageType(msg) {
  const m = unwrapMessage(msg.message || {});
  if (m.deviceSentMessage?.message) return messageType({ message: m.deviceSentMessage.message });
  if (m.protocolMessage?.editedMessage) return messageType({ message: m.protocolMessage.editedMessage });
  return Object.keys(m).find(k => k !== 'messageContextInfo') || 'unknown';
}
function describeMessage(msg) {
  const text = getMessageText(msg);
  if (text) return text;
  const m = unwrapMessage(msg.message || {});
  if (m.deviceSentMessage?.message) return describeMessage({ message: m.deviceSentMessage.message });
  const t = messageType(msg);
  const map = {
    imageMessage: '[imagem sem legenda]', videoMessage: '[vídeo sem legenda]', audioMessage: '[áudio/voz]',
    documentMessage: '[documento sem legenda]', stickerMessage: '[figurinha]', contactMessage: '[contato]',
    contactsArrayMessage: '[contatos]', locationMessage: '[localização]', liveLocationMessage: '[localização ao vivo]',
    reactionMessage: '[reação]', pollCreationMessage: '[enquete]', interactiveMessage: '[mensagem interativa sem texto visível]',
    secretEncryptedMessage: '[mensagem criptografada sem texto liberado pelo WhatsApp]', interactiveResponseMessage: '[resposta interativa sem texto visível]', buttonsResponseMessage: '[resposta de botão sem texto visível]',
    listResponseMessage: '[resposta de lista sem texto visível]', protocolMessage: '[mensagem de sistema do WhatsApp]',
    senderKeyDistributionMessage: '[sincronização do WhatsApp]', messageContextInfo: '[contexto do WhatsApp]'
  };
  return map[t] || `[${t || 'mensagem sem texto visível'}]`;
}

function mediaInfoFromMessage(msg) {
  const m = unwrapMessage(msg.message || {});
  const candidates = [
    ['audio', m.audioMessage], ['image', m.imageMessage], ['video', m.videoMessage], ['document', m.documentMessage], ['sticker', m.stickerMessage]
  ];
  for (const [kind, media] of candidates) {
    if (media) {
      const mimetype = media.mimetype || (kind === 'audio' ? 'audio/ogg' : 'application/octet-stream');
      return { kind, media, mimetype, fileName: media.fileName || '' };
    }
  }
  return null;
}
function extFromMime(mimetype, kind) {
  const mt = String(mimetype || '').toLowerCase();
  if (mt.includes('ogg')) return '.ogg';
  if (mt.includes('opus')) return '.ogg';
  if (mt.includes('mpeg') || mt.includes('mp3')) return '.mp3';
  if (mt.includes('wav')) return '.wav';
  if (mt.includes('mp4')) return kind === 'audio' ? '.m4a' : '.mp4';
  if (mt.includes('jpeg') || mt.includes('jpg')) return '.jpg';
  if (mt.includes('png')) return '.png';
  if (mt.includes('webp')) return '.webp';
  if (mt.includes('pdf')) return '.pdf';
  return kind === 'audio' ? '.ogg' : '.bin';
}
async function streamToBufferSafe(stream, ms = 20000) {
  return await withTimeout((async () => {
    const chunks = [];
    for await (const chunk of stream) chunks.push(chunk);
    return Buffer.concat(chunks);
  })(), ms, 'download media stream');
}
async function saveIncomingMedia(m, item) {
  const key = String(item?.id || '') + '|' + String(item?.jid || '');
  if (key && mediaDownloadActive.has(key)) return;
  if (key) mediaDownloadActive.add(key);
  try {
    if (!downloadContentFromMessage) return;
    const info = mediaInfoFromMessage(m);
    if (!info || !info.media) return;

    item.media_download_status = 'downloading';
    item.media_kind = info.kind;
    item.mimetype = info.mimetype;
    const idxPending = messages.findIndex(x => x.id === item.id && x.jid === item.jid);
    if (idxPending >= 0) messages[idxPending] = { ...messages[idxPending], ...item };
    saveDb();

    const stream = await withTimeout(downloadContentFromMessage(info.media, info.kind), 12000, 'start media download');
    const buffer = await streamToBufferSafe(stream, info.kind === 'audio' ? 25000 : 30000);
    if (!buffer || buffer.length < 10) throw new Error('media buffer empty');
    const safeId = String(item.id || Date.now()).replace(/[^a-zA-Z0-9_-]/g, '_');
    const ext = extFromMime(info.mimetype, info.kind);
    const filePath = path.join(MEDIA_DIR, `${Date.now()}_${info.kind}_${safeId}${ext}`);
    fs.writeFileSync(filePath, buffer);
    item.media_path = filePath;
    item.mimetype = info.mimetype;
    item.media_kind = info.kind;
    item.media_size = buffer.length;
    item.media_download_status = 'saved';
    item.media_saved_at = new Date().toISOString();
    if (info.fileName) item.file_name = info.fileName;
    const idx = messages.findIndex(x => x.id === item.id && x.jid === item.jid);
    if (idx >= 0) messages[idx] = { ...messages[idx], ...item };
    saveDb();
    saveStatus({ last_event: 'media_saved', message: `Mídia recebida salva: ${info.kind}` });
    // Se for áudio ou imagem recebida, o agente só entra agora na fila, já com arquivo disponível
    // para transcrição/análise. Para áudio, também refazemos o alerta com mídia já salva.
    if ((info.kind === 'audio' || info.kind === 'image') && !item.from_me) {
      if (info.kind === 'audio') {
        try { queueAlert(item); } catch (_) {}
      }
      try { queueAgentMessage(item); } catch (_) {}
    }
    log('media saved', info.kind, filePath, buffer.length);
  } catch (e) {
    try {
      const idx = messages.findIndex(x => x.id === item.id && x.jid === item.jid);
      if (idx >= 0) {
        messages[idx].media_download_status = 'failed';
        messages[idx].media_error = String(e.message || e);
        saveDb();
      }
    } catch (_) {}
    log('saveIncomingMedia error', e?.stack || e);
  } finally {
    if (key) mediaDownloadActive.delete(key);
  }
}

function betterMessage(newItem, oldItem) {
  if (!oldItem) return newItem;
  const oldText = String(oldItem.text || '');
  const newText = String(newItem.text || '');
  const oldBad = /^\[(secretEncryptedMessage|protocolMessage|unknown|senderKeyDistributionMessage|messageContextInfo|mensagem criptografada|mensagem de sistema|sincronização|contexto)/i.test(oldText) || ['secretEncryptedMessage','protocolMessage','unknown'].includes(String(oldItem.type || ''));
  const newGood = isMeaningfulMessageItem(newItem) || newText.length > oldText.length || newItem.media_path;
  if (oldBad && newGood) return { ...oldItem, ...newItem };
  if (newItem.media_path && !oldItem.media_path) return { ...oldItem, ...newItem };
  if (newText && newText.length > oldText.length && !/^\[/.test(newText)) return { ...oldItem, ...newItem };
  return { ...oldItem, updated_at: new Date().toISOString() };
}

function isMeaningfulMessageItem(item) {
  if (!item) return false;
  const text = String(item.text || '').trim();
  const type = String(item.type || '');
  if (!text) return false;
  const badTypes = new Set(['protocolMessage', 'senderKeyDistributionMessage', 'messageContextInfo', 'unknown', 'secretEncryptedMessage']);
  if (badTypes.has(type)) return false;
  if (/^\[(protocolMessage|unknown|senderKeyDistributionMessage|messageContextInfo|secretEncryptedMessage)\]$/i.test(text)) return false;
  if (/^\[(mensagem de sistema do WhatsApp|sincronização do WhatsApp|contexto do WhatsApp|mensagem interativa sem texto visível|resposta interativa sem texto visível)\]$/i.test(text)) return false;
  return true;
}

function matchWatchContact(item, watch) {
  const target = normalizeText(watch || '');
  if (!target) return false;
  const vals = [item.name, item.chat_name, item.sender_jid, item.jid].filter(Boolean).map(v => normalizeText(v));
  if (vals.some(v => v === target || v.includes(target) || target.includes(v))) return true;
  const ali = aliases[target];
  if (ali && vals.some(v => v === normalizeText(ali.name) || v === normalizeText(ali.jid))) return true;
  return false;
}
function watchListMatches(item) {
  const list = Array.isArray(watchConfig.contacts) ? watchConfig.contacts : [];
  return list.some(w => matchWatchContact(item, w.name || w.jid || w.query || w));
}
function isAudioMessageItem(item) {
  const text = String(item?.text || '').toLowerCase();
  const type = String(item?.type || '').toLowerCase();
  const kind = String(item?.media_kind || '').toLowerCase();
  return kind === 'audio' || type.includes('audio') || text.includes('[áudio') || text.includes('[audio') || text.includes('audio/voz');
}
function isImageMessageItem(item) {
  const text = String(item?.text || '').toLowerCase();
  const type = String(item?.type || '').toLowerCase();
  const kind = String(item?.media_kind || '').toLowerCase();
  return kind === 'image' || type.includes('image') || text.includes('[imagem');
}
function shouldAlertMessage(item) {
  if (!watchConfig || !watchConfig.enabled) return false;
  if (!item || item.from_me) return false;
  if (!isMeaningfulMessageItem(item)) return false;

  // Áudio recebido precisa primeiro ser salvo. Assim o Jarvis pode falar o conteúdo transcrito direto
  // e não apenas avisar "[áudio recebido]" antes do arquivo ficar disponível.
  if (isAudioMessageItem(item) && !item.media_path && String(item.media_download_status || '').toLowerCase() !== 'failed') return false;

  const isGroup = Boolean(item.is_group || String(item.jid || '').endsWith('@g.us'));
  const idKey = `${item.jid || ''}:${item.sender_jid || ''}:${item.id || item.timestamp || ''}`;
  if (alertSeen[idKey]) return false;

  const mode = String(watchConfig.mode || '').toLowerCase();
  if (['any', 'all', 'todos', 'qualquer'].includes(mode)) return true;
  if (['private', 'privado', 'privados', 'contacts_only', 'contatos_privados'].includes(mode)) return !isGroup;
  if (['groups', 'group_only', 'grupos', 'somente_grupos'].includes(mode)) return isGroup;
  if (['contact', 'contacts', 'contato', 'specific_contact'].includes(mode)) return !isGroup && watchListMatches(item);
  if (['group', 'specific_group', 'grupo'].includes(mode)) return isGroup && watchListMatches(item);
  if (mode === 'contacts_any') return watchListMatches(item);
  return false;
}
function queueAlert(item) {
  try {
    if (!shouldAlertMessage(item)) return;
    const idKey = `${item.jid || ''}:${item.sender_jid || ''}:${item.id || item.timestamp || ''}`;
    alertSeen[idKey] = true;
    const isGroup = Boolean(item.is_group || String(item.jid || '').endsWith('@g.us'));
    let alertName = item.name || item.sender_name || item.sender_jid || 'contato';
    const alertChatName = item.chat_name || (isGroup ? friendlyNameForJid(item.jid) : (item.name || item.jid || ''));
    if (isGroup && (normalizeText(alertName) === normalizeText(alertChatName) || _looksLikeRawParticipantName(alertName, item.sender_jid))) {
      alertName = 'participante do grupo';
    }
    const alert = {
      id: idKey,
      name: alertName,
      chat_name: alertChatName,
      jid: item.jid,
      sender_jid: item.sender_jid,
      text: String(item.text || '').slice(0, 500),
      is_group: isGroup,
      chat_type: isGroup ? 'group' : 'private',
      type: item.type || 'message',
      media_kind: item.media_kind || '',
      media_path: item.media_path || '',
      media_download_status: item.media_download_status || '',
      timestamp: item.timestamp || Math.floor(Date.now()/1000),
      created_at: new Date().toISOString(),
    };
    alertQueue.push(alert);
    if (alertQueue.length > 200) alertQueue = alertQueue.slice(-200);
    saveDb();
    saveStatus({ last_event:'whatsapp_alert', message:`Alerta WhatsApp de ${alert.name}.` });
  } catch(e) { log('queueAlert error', e.message); }
}

function queueAgentMessage(item) {
  try {
    if (!item || item.from_me) return;
    if (!isMeaningfulMessageItem(item)) return;
    // For audio/image, wait until media has been saved so the Python side can transcribe/analyze before replying.
    if ((isAudioMessageItem(item) || isImageMessageItem(item)) && !item.media_path && String(item.media_download_status || '').toLowerCase() !== 'failed') return;
    const idKey = `${item.jid || ''}:${item.sender_jid || ''}:${item.id || item.timestamp || ''}`;
    if (agentSeen[idKey]) return;
    agentSeen[idKey] = true;
    const isGroup = Boolean(item.is_group || String(item.jid || '').endsWith('@g.us'));
    let agentName = item.name || item.sender_name || item.sender_jid || 'contato';
    const agentChatName = item.chat_name || (isGroup ? friendlyNameForJid(item.jid) : (item.name || item.jid || ''));
    if (isGroup && (normalizeText(agentName) === normalizeText(agentChatName) || _looksLikeRawParticipantName(agentName, item.sender_jid))) {
      agentName = 'participante do grupo';
    }
    const ev = {
      id: idKey,
      name: agentName,
      chat_name: agentChatName,
      jid: item.jid,
      sender_jid: item.sender_jid,
      text: String(item.text || '').slice(0, 1200),
      is_group: isGroup,
      chat_type: isGroup ? 'group' : 'private',
      type: item.type || 'message',
      media_kind: item.media_kind || '',
      media_path: item.media_path || '',
      timestamp: item.timestamp || Math.floor(Date.now()/1000),
      created_at: new Date().toISOString(),
    };
    agentQueue.push(ev);
    if (agentQueue.length > 500) agentQueue = agentQueue.slice(-500);
    saveDb();
  } catch(e) { log('queueAgentMessage error', e.message); }
}

function setWatchAny() {
  watchConfig = { enabled: true, mode: 'any', contacts: [], include_groups: true, target_type: 'all', updated_at: new Date().toISOString() };
  alertQueue = [];
  saveDb();
  return 'Alerta ativado: vou avisar quando qualquer pessoa, contato privado ou grupo enviar mensagem no WhatsApp.';
}
function setWatchPrivateOnly() {
  watchConfig = { enabled: true, mode: 'private', contacts: [], include_groups: false, target_type: 'private', updated_at: new Date().toISOString() };
  alertQueue = [];
  saveDb();
  return 'Alerta ativado: vou avisar somente mensagens privadas de contatos. Mensagens de grupos serão ignoradas.';
}
function setWatchGroupsOnly() {
  watchConfig = { enabled: true, mode: 'groups', contacts: [], include_groups: true, target_type: 'groups', updated_at: new Date().toISOString() };
  alertQueue = [];
  saveDb();
  return 'Alerta ativado: vou avisar somente mensagens de grupos. Mensagens privadas serão ignoradas.';
}
function setWatchSpecific(rawName, wantGroup=false) {
  const raw = String(rawName || '').trim();
  if (!raw) throw new Error(wantGroup ? 'Informe o nome do grupo para ativar o alerta.' : 'Informe o nome do contato para ativar o alerta.');
  let found = searchContacts(raw, 8).find(x => wantGroup ? String(x.jid || '').endsWith('@g.us') : !String(x.jid || '').endsWith('@g.us')) || searchContacts(raw, 1)[0];
  const entry = found ? { name: found.name, jid: found.jid || '', query: raw } : { name: raw, jid: '', query: raw };
  watchConfig = { enabled: true, mode: wantGroup ? 'group' : 'contact', contacts: [entry], include_groups: true, target_type: wantGroup ? 'specific_group' : 'specific_contact', updated_at: new Date().toISOString() };
  alertQueue = [];
  saveDb();
  return wantGroup
    ? `Alerta ativado: vou avisar somente quando chegar mensagem no grupo ${entry.name || raw}.`
    : `Alerta ativado: vou avisar somente quando ${entry.name || raw} enviar mensagem no WhatsApp.`;
}
function setWatchContact(contactName) { return setWatchSpecific(contactName, false); }
function setWatchGroup(groupName) { return setWatchSpecific(groupName, true); }
function stopWatch() {
  watchConfig = { enabled: false, mode: 'off', contacts: [], include_groups: true, target_type: 'off', updated_at: new Date().toISOString() };
  alertQueue = [];
  saveDb();
  return 'Alertas de mensagens do WhatsApp desativados.';
}

function rememberMessage(m) {
  try {
    const jid = m.key?.remoteJid;
    if (!jid || jid === 'status@broadcast') return;
    // Sem conteudo (nao decifrada, troca de chave, contexto): nao e mensagem
    // que alguem possa ler, e em rajada ela expulsava as de verdade do banco.
    // Se a mesma mensagem chegar DEPOIS decifrada, ela entra normalmente.
    const tipoRecebido = messageType(m);
    if (REGISTRO_VAZIO.has(tipoRecebido)) return;
    if (tipoRecebido === 'protocolMessage' && !unwrapMessage(m.message || {}).protocolMessage?.editedMessage) return;
    const fromMe = Boolean(m.key?.fromMe);
    const senderJid = fromMe ? (sock?.user?.id || 'me') : (m.key?.participant || jid);
    const isGroup = String(jid || '').endsWith('@g.us');
    const currentChatName = friendlyNameForJid(jid);
    const rawChatName = currentChatName && !String(currentChatName).includes('@') ? currentChatName : jid;
    const chatName = isGroup ? rawChatName : (rawChatName && !String(rawChatName).includes('@') ? rawChatName : (m.pushName || friendlyNameForJid(senderJid) || jid));
    const savedContactName = contacts[senderJid]?.name_source === 'address_book' ? contacts[senderJid]?.name : '';
    const personName = fromMe ? 'Você' : (isGroup ? chooseGroupSenderName(m, senderJid, chatName) : (savedContactName || m.pushName || friendlyNameForJid(senderJid) || chatName || senderJid));
    const item = { id: m.key?.id, jid, sender_jid: senderJid, from_me: fromMe, name: personName, chat_name: chatName, is_group: isGroup, chat_type: isGroup ? 'group' : 'private', text: describeMessage(m), type: messageType(m), timestamp: Number(m.messageTimestamp || Math.floor(Date.now()/1000)), key: m.key };
    if (item.id) {
      const idx = messages.findIndex(x => x.id === item.id && x.jid === item.jid);
      if (idx >= 0) {
        messages[idx] = betterMessage(item, messages[idx]);
        if (mediaInfoFromMessage(m) && !messages[idx].media_path) saveIncomingMedia(m, messages[idx]).catch(()=>{});
        saveDb();
        saveStatus({ last_event: 'message_update', message: 'Mensagem do WhatsApp atualizada.' });
        if (!messages[idx].from_me) { queueAlert(messages[idx]); queueAgentMessage(messages[idx]); }
        return;
      }
    }
    messages.push(item);
    if (mediaInfoFromMessage(m)) saveIncomingMedia(m, item).catch(()=>{});
    if (jid.endsWith('@g.us')) rememberGroup(jid, { subject: chatName, name: chatName }); else rememberChat(jid, { name: chatName, notify: personName, pushName: personName });
    if (!fromMe) rememberContact(senderJid, { notify: personName, pushName: personName }, { source: 'message' });
    // Aprender nome automaticamente apenas em conversa privada. Participantes
    // vistos dentro de grupos nÃ£o entram como destinatÃ¡rios automÃ¡ticos.
    if (!fromMe && !isGroup) autoAliasFromName(personName, senderJid, 'private_message');
    if (!isGroup) autoAliasFromName(chatName, jid, 'private_chat');
    saveDb();
    saveStatus({ last_event: 'message', message: fromMe ? 'Mensagem enviada registrada.' : (isGroup ? `Nova mensagem no grupo ${chatName} de ${personName}.` : `Nova mensagem privada de ${personName}.`) });
    if (!fromMe) { queueAlert(item); queueAgentMessage(item); }
  } catch (e) { log('rememberMessage error', e.message); }
}
function searchContacts(q, limit = 10) {
  const target = normalizeText(q);
  const all = [];
  const privateJids = new Set();
  const groupParticipantJids = new Set();
  for (const m of (messages || [])) {
    const messageJid = String(m?.jid || '');
    const senderJid = String(m?.sender_jid || '');
    if (m?.is_group || isGroupJid(messageJid)) {
      if (isPersonJid(senderJid)) groupParticipantJids.add(senderJid);
    } else {
      if (isPersonJid(messageJid)) privateJids.add(messageJid);
      if (isPersonJid(senderJid)) privateJids.add(senderJid);
    }
  }
  const isGroupOnlyIdentity = (jid) => groupParticipantJids.has(String(jid || '')) && !privateJids.has(String(jid || ''));
  const textScore = (name) => {
    const normName = normalizeText(name);
    const normAll = normalizeText(String(name || ''));
    if (!target) return 1;
    if (normName === target) return 140;
    if (normName.startsWith(target)) return 120;
    if (normName.includes(target)) return 105;
    if (target.includes(normName) && normName.length >= 3) return 80;
    return normAll.includes(target) ? 65 : 0;
  };
  const add = (jid, name, priority, extra = {}) => {
    if (!jid || !name || jid === 'status@broadcast') return;
    const matched = textScore(name);
    if (matched > 0) all.push({ jid, name, score: matched + priority, is_group: isGroupJid(jid), ...extra });
  };
  for (const a of Object.values(aliases || {})) {
    const name = a.name || a.jid;
    const isManual = a.auto !== true;
    if (!isManual && !isGroupJid(a.jid) && isGroupOnlyIdentity(a.jid) && a.source !== 'address_book') continue;
    const priority = isManual ? 220 : (a.source === 'address_book' ? 170 : (isGroupJid(a.jid) ? 0 : 35));
    add(a.jid, name, priority, { alias: true, source: a.source || (isManual ? 'manual' : 'legacy_auto') });
  }
  // HistÃ³rico privado ajuda a localizar nÃ£o salvos; nomes vistos somente em
  // grupos ficam de fora para nÃ£o serem confundidos com a agenda.
  for (const m of (messages || []).slice(-600)) {
    if (m?.is_group || isGroupJid(m?.jid)) continue;
    const candidates = [
      { jid: m.sender_jid || m.jid, name: m.name },
      { jid: m.jid, name: m.chat_name },
    ];
    for (const c of candidates) {
      add(c.jid, c.name, 25, { source: 'private_messages' });
    }
  }
  for (const [jid, c] of Object.entries(contacts || {})) {
    const savedInAddressBook = c?.name_source === 'address_book';
    const groupOnly = isGroupOnlyIdentity(jid);
    if (c?.name && (savedInAddressBook || !groupOnly)) add(jid, c.name, savedInAddressBook ? 180 : 75, { source: savedInAddressBook ? 'address_book' : 'contact_name' });
    if (!groupOnly) {
      if (c?.notify && normalizeText(c.notify) !== normalizeText(c.name)) add(jid, c.notify, 45, { source: 'profile_name' });
      if (c?.pushName && normalizeText(c.pushName) !== normalizeText(c.name) && normalizeText(c.pushName) !== normalizeText(c.notify)) add(jid, c.pushName, 40, { source: 'profile_name' });
    }
  }
  for (const [jid, c] of Object.entries(chats || {})) {
    if (isGroupJid(jid)) continue;
    add(jid, displayName(c, jid), 50, { source: 'private_chat' });
  }
  for (const [jid, g] of Object.entries(groups || {})) {
    add(jid, displayName(g, jid), 0, { source: 'group' });
  }
  const dedup = new Map();
  for (const item of all.sort((a,b) => b.score - a.score || String(a.name).localeCompare(String(b.name)))) {
    const k = String(item.jid || item.name || '');
    if (!dedup.has(k)) dedup.set(k, item);
  }
  return Array.from(dedup.values()).slice(0, limit);
}
async function resolveJid(to) {
  const raw = String(to || '').trim();
  if (!raw) throw new Error('Recipient is empty');
  const alias = aliases[aliasKey(raw)];
  // Alias criado explicitamente pelo usuÃ¡rio continua absoluto. Alias aprendido
  // automaticamente participa do ranking atualizado da agenda.
  if (alias && alias.jid && alias.auto !== true) return alias.jid;
  if (raw.includes('@s.whatsapp.net') || raw.includes('@g.us') || raw.includes('@lid')) return raw;
  const found = searchContacts(raw, 5);
  if (found.length) return found[0].jid;
  const histAlias = aliasFromMessageHistory(raw);
  if (histAlias && histAlias.jid) return histAlias.jid;
  const num = normalizeBrazilNumber(raw);
  if (num.length >= 11) {
    let jid = `${num}@s.whatsapp.net`;
    try {
      if (sock?.onWhatsApp) {
        const exists = await withTimeout(sock.onWhatsApp(num), 4000, 'onWhatsApp');
        if (exists && exists[0] && exists[0].exists) jid = exists[0].jid || jid;
      }
    } catch (e) { log('onWhatsApp failed/timeout', e.message); }
    if (lastFailedName) {
      try { saveAlias(lastFailedName, jid); log('auto alias saved after number', lastFailedName, jid); lastFailedName = ''; } catch(e) { log('auto alias failed', e.message); }
    }
    return jid;
  }
  lastFailedName = raw;
  saveStatus({ last_event:'contact_not_found', message:`Contato não encontrado: ${raw}. Se informar o número agora, vou salvar esse nome automaticamente.` });
  const suggestions = searchContacts('', 8).map(x => x.name).filter(Boolean).join(', ');
  throw new Error(`Contact not found: ${raw}. Contatos: ${contactCount()}, conversas: ${chatCount()}, grupos: ${groupCount()}. ${suggestions ? 'Sugestões: ' + suggestions : 'Use número com DDD ou mande/receba uma mensagem desse contato para sincronizar.'}`);
}
function clearQrWatchdog() { if (qrWatchdog) clearTimeout(qrWatchdog); qrWatchdog = null; }
function cleanSessionFiles(clearAuth = false) {
  try { if (fs.existsSync(QR_FILE)) fs.unlinkSync(QR_FILE); } catch (_) {}
  try { if (fs.existsSync(STATUS_FILE)) fs.unlinkSync(STATUS_FILE); } catch (_) {}
  lastQr = '';
  if (clearAuth) {
    encerrarConexaoAtual();
    try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (_) {}
    freshLoginRequired = false;
    lastDisconnectReason = '';
    log('Auth session cleaned for fresh WhatsApp login.');
  }
}
function scheduleQrWatchdog() {
  clearQrWatchdog();
  qrWatchdog = setTimeout(() => {
    if (currentConnected() || lastQr) return;
    lastEvent = 'qr_timeout';
    saveStatus({ connected: false, has_qr: false, last_event: lastEvent, message: 'QR Code ainda não foi gerado. Feche o Mark-XLVIII, rode 8_instalar_whatsapp_bridge.bat e tente de novo.', error: 'QR timeout' });
    log('QR timeout. No qr event received from Baileys.');
  }, 45000);
}
async function start({ fresh = false } = {}) {
  if (freshLoginRequired) fresh = true;
  if (starting) return;
  starting = true;
  try {
    clearQrWatchdog();
    // A conexao anterior (se houver) morre ANTES de a nova nascer. Ver
    // `geracao` la em cima.
    encerrarConexaoAtual();
    if (fresh) cleanSessionFiles(true); else cleanSessionFiles(false);
    // Depois da limpeza: `cleanSessionFiles(true)` tambem encerra (e conta).
    const minha = geracao;
    const vale = () => minha === geracao;
    manualLogoutInProgress = false;
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    try { if (!fs.existsSync(LOG_FILE)) fs.writeFileSync(LOG_FILE, ''); } catch (_) {}
    connected = false;
    lastEvent = 'starting';
    saveStatus({ connected: false, has_qr: false, message: 'Iniciando ponte WhatsApp. Gerando QR Code...', last_event: 'starting' });
    log('Starting bridge', { port: PORT, root: ROOT, fresh });
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    const { version } = await withTimeout(fetchLatestBaileysVersion(), 15000, 'fetchLatestBaileysVersion');
    log('Baileys version', version);
    if (!vale()) { log('Start abandoned: a newer connection was requested meanwhile.'); return; }
    sock = makeWASocket({
      version,
      auth: state,
      logger: pino({ level: 'silent' }),
      printQRInTerminal: true,
      // O SISTEMA QUE O CELULAR MOSTRA em "Aparelhos conectados" (27/09/2026).
      // Era sempre Windows — no cliente do Big Linux o WhatsApp dele mostrava
      // um aparelho Windows que ele nao tem. Agora e o sistema de verdade.
      // (Nao muda o historico: o syncFullHistory esta desligado logo abaixo.)
      browser: process.platform === 'linux'
        ? ['Linux', 'Mark-XLVIII', require('os').release()]
        : process.platform === 'darwin'
          ? Browsers.macOS('Mark-XLVIII')
          : Browsers.windows('Mark-XLVIII'),
      connectTimeoutMs: 30000,
      defaultQueryTimeoutMs: 15000,
      qrTimeout: 60000,
      markOnlineOnConnect: false,
      // MODO SEGURO: não forçar histórico completo. Forçar syncFullHistory derruba a conexão em alguns WhatsApp.
      syncFullHistory: false,
      fireInitQueries: true,
      generateHighQualityLinkPreview: false,
    });
    // Credencial de conexao velha nao se grava: ela estragaria a da nova.
    sock.ev.on('creds.update', (...a) => { if (vale()) return saveCreds(...a); });
    sock.ev.on('contacts.upsert', (items) => { for (const c of items || []) if (c?.id) rememberContact(c.id, c, { source: 'address_book' }); saveDb(); });
    sock.ev.on('contacts.update', (items) => { for (const c of items || []) if (c?.id) rememberContact(c.id, c, { source: 'address_book' }); saveDb(); });
    sock.ev.on('chats.upsert', (items) => { for (const c of items || []) { if (!c?.id) continue; if (c.id.endsWith('@g.us')) rememberGroup(c.id, c); else rememberChat(c.id, c); } saveDb(); });
    sock.ev.on('chats.update', (items) => { for (const c of items || []) { if (!c?.id) continue; if (c.id.endsWith('@g.us')) rememberGroup(c.id, c); else rememberChat(c.id, c); } saveDb(); });
    sock.ev.on('groups.update', (items) => { for (const g of items || []) { if (g?.id) rememberGroup(g.id, { ...g, name: g.subject, subject: g.subject }); } saveDb(); });
    sock.ev.on('messaging-history.set', ({ chats: hc, contacts: hct, messages: hm }) => {
      try {
        for (const c of hct || []) if (c?.id) rememberContact(c.id, c, { source: 'address_book' });
        for (const c of hc || []) if (c?.id) { if (c.id.endsWith('@g.us')) rememberGroup(c.id, c); else rememberChat(c.id, c); }
        for (const m of hm || []) rememberMessage(m);
        saveDb();
        saveStatus({ message: `Sincronizado. Contatos: ${contactCount()}, conversas: ${chatCount()}, mensagens: ${msgCount()}.`, last_event: 'history_sync' });
      } catch (e) { log('history event error', e.message); }
    });
    sock.ev.on('messages.upsert', ({ messages: newMessages }) => { for (const m of newMessages || []) rememberMessage(m); });
    sock.ev.on('messages.update', (updates) => {
      try {
        for (const u of updates || []) {
          if (u?.update?.message) rememberMessage({ key: u.key, message: u.update.message, messageTimestamp: Math.floor(Date.now()/1000), pushName: u.update.pushName || '' });
        }
      } catch(e) { log('messages.update error', e.message); }
    });
    sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;
      if (!vale()) {
        // Conexao antiga falando depois de encerrada: so fica no registro.
        if (connection || qr) log('old connection.update ignored', { connection, hasQr: Boolean(qr), geracao: minha, atual: geracao });
        return;
      }
      log('connection.update', { connection, hasQr: Boolean(qr), statusCode: lastDisconnect?.error?.output?.statusCode });
      if (qr) {
        clearQrWatchdog();
        lastQr = qr;
        connected = false;
        lastEvent = 'qr';
        await QRCode.toFile(QR_FILE, qr, { width: 420, margin: 2 });
        saveStatus({ connected: false, has_qr: true, message: 'QR Code gerado. Escaneie com o WhatsApp.', last_event: 'qr' });
        log('QR generated', QR_FILE);
      }
      if (connection === 'open') {
        clearQrWatchdog();
        connected = true;
        freshLoginRequired = false;
        lastDisconnectReason = '';
        lastQr = '';
        lastEvent = 'connected';
        try { if (fs.existsSync(QR_FILE)) fs.unlinkSync(QR_FILE); } catch (_) {}
        saveStatus({ connected: true, has_qr: false, message: 'WhatsApp conectado com sucesso. Leitura de novas mensagens ativa.', last_event: 'connected', connected_at: new Date().toISOString(), safe_sync: true });
        log('Connected.');
        refreshGroups(6000).catch(e => log('background groups failed', e.message));
        refreshAddressBook(20000).catch(e => log('background address book failed', e.message));
      }
      if (connection === 'close') {
        clearQrWatchdog();
        connected = false;
        lastQr = '';
        lastEvent = 'disconnected';
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        const userLoggedOut = statusCode === DisconnectReason.loggedOut || manualLogoutInProgress;
        const shouldReconnect = !userLoggedOut;
        lastDisconnectReason = userLoggedOut ? 'user_logged_out' : (shouldReconnect ? 'connection_lost' : 'disconnected');
        if (userLoggedOut) {
          // Quando o usuário desconecta pelo celular, as credenciais locais ficam inválidas.
          // Limpa imediatamente para o próximo pedido de conexão gerar um QR 100% novo.
          freshLoginRequired = true;
          encerrarConexaoAtual();
          try { if (fs.existsSync(QR_FILE)) fs.unlinkSync(QR_FILE); } catch (_) {}
          try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (_) {}
          lastQr = '';
        }
        const disconnectMessage = userLoggedOut
          ? 'WhatsApp desconectado pelo usuário. Sessão antiga limpa; ao conectar novamente será gerado um QR Code novo.'
          : 'WhatsApp desconectado.';
        saveStatus({ connected: false, has_qr: false, message: disconnectMessage, statusCode, disconnect_reason: lastDisconnectReason, fresh_login_required: freshLoginRequired, last_event: 'disconnected' });
        log('Disconnected.', statusCode, lastDisconnectReason, 'reconnect:', shouldReconnect, 'freshLoginRequired:', freshLoginRequired);
        manualLogoutInProgress = false;
        // 440 = "outra sessao substituiu esta": outro programa (ou outro PC)
        // com a MESMA sessao. Religar em 3 s so briga com ele em laco; espera
        // mais. O resto (rede caiu, 515 do pareamento) volta em 3 s como antes.
        if (shouldReconnect) agendarReconexao(minha, statusCode === 440 ? 20000 : 3000);
      }
    });
    scheduleQrWatchdog();
    saveStatus({ connected: false, has_qr: false, message: 'Ponte WhatsApp iniciada. Aguardando QR Code...', last_event: 'started' });
  } catch (err) {
    log('Start failed', err?.stack || err);
    lastEvent = 'error';
    saveStatus({ connected: false, has_qr: false, message: 'Falha ao iniciar ponte WhatsApp.', error: String(err.message || err), last_event: 'error' });
  } finally { starting = false; }
}
async function convertAudioToOpus(inputPath) {
  if (!ffmpegPath) throw new Error('ffmpeg-static não instalado. Rode 8_instalar_whatsapp_bridge.bat novamente.');
  const out = path.join(MEDIA_DIR, `voice_${Date.now()}.ogg`);
  await new Promise((resolve, reject) => {
    const args = ['-y', '-i', inputPath, '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'libopus', '-b:a', '32k', '-f', 'ogg', out];
    const p = spawn(ffmpegPath, args, { windowsHide: true });
    let err = '';
    p.stderr.on('data', d => err += d.toString());
    p.on('error', reject);
    p.on('close', code => code === 0 && fs.existsSync(out) && fs.statSync(out).size > 500 ? resolve() : reject(new Error('ffmpeg falhou ao converter áudio: ' + err.slice(-400))));
  });
  return out;
}
function mediaPayload(filePath, caption = '') {
  if (!filePath || !fs.existsSync(filePath)) throw new Error('Arquivo não encontrado: ' + filePath);
  const st = fs.statSync(filePath);
  if (st.isDirectory()) throw new Error('Recebi uma pasta sem compactar. Compacte em ZIP antes de enviar pelo WhatsApp.');
  if (MAX_SEND_BYTES && st.size > MAX_SEND_BYTES) {
    const mb = (st.size / (1024 * 1024)).toFixed(2);
    const limitMb = Math.round(MAX_SEND_BYTES / (1024 * 1024));
    throw new Error(`Arquivo muito grande para enviar pelo WhatsApp nesta configuração: ${path.basename(filePath)} (${mb} MB). Limite atual: ${limitMb} MB.`);
  }
  const mt = (mime && mime.lookup(filePath)) || 'application/octet-stream';
  const fileName = path.basename(filePath);
  if (mt.startsWith('image/')) return { image: { url: filePath }, caption: caption || '' };
  if (mt.startsWith('video/')) return { video: { url: filePath }, caption: caption || '', mimetype: mt };
  if (mt.startsWith('audio/')) return { audio: { url: filePath }, mimetype: mt, ptt: false };
  return { document: { url: filePath }, fileName, mimetype: mt, caption: caption || '' };
}
async function refreshGroups(timeoutMs = 6000) {
  if (!currentConnected()) throw new Error('WhatsApp is not connected.');
  if (!sock.groupFetchAllParticipating) return Object.values(groups);
  const data = await withTimeout(sock.groupFetchAllParticipating(), timeoutMs, 'groupFetchAllParticipating');
  for (const [jid, g] of Object.entries(data || {})) rememberGroup(jid, { ...g, name: g.subject, subject: g.subject });
  saveDb();
  saveStatus({ last_event: 'groups', message: `Grupos atualizados: ${groupCount()}.` });
  return Object.values(groups);
}

async function refreshAddressBook(timeoutMs = 20000) {
  if (!currentConnected() || !sock?.resyncAppState || contactSyncInFlight) return false;
  contactSyncInFlight = true;
  try {
    // A colecao "critical_unblock_low" contem as alteracoes da agenda (contactAction).
    // Isto nao solicita historico completo de mensagens.
    await withTimeout(sock.resyncAppState(['critical_unblock_low'], false), timeoutMs, 'address book sync');
    saveDb();
    log('Address book app-state synchronized.');
    return true;
  } catch (e) {
    log('address book sync failed/timeout', e.message);
    return false;
  } finally {
    contactSyncInFlight = false;
  }
}


async function resolveGroupJid(groupName) {
  if (!currentConnected()) throw new Error('WhatsApp is not connected.');
  const raw = String(groupName || '').trim();
  if (!raw) {
    if (pendingLeaveGroup && pendingLeaveGroup.jid) return pendingLeaveGroup;
    throw new Error('Nome do grupo é obrigatório.');
  }
  if (raw.endsWith('@g.us')) {
    const g = groups[raw] || chats[raw] || {};
    return { jid: raw, name: g.subject || g.name || raw };
  }
  try { await refreshGroups(6000); } catch(e) { log('resolveGroupJid refresh failed', e.message); }
  const target = normalizeText(raw);
  const candidates = [];
  for (const [jid, g] of Object.entries(groups || {})) {
    const name = g.subject || g.name || jid;
    const score = fuzzyNameScore(name + ' ' + jid, raw);
    if (score >= 45) candidates.push({ jid, name, score });
  }
  // Also search normal resolver results, but only group JIDs.
  for (const item of searchContacts(raw, 30)) {
    if (!String(item.jid || '').endsWith('@g.us')) continue;
    const score = Math.max(item.score || 0, fuzzyNameScore(item.name || item.jid, raw));
    if (score >= 45) candidates.push({ jid: item.jid, name: item.name || item.jid, score });
  }
  const dedup = new Map();
  for (const c of candidates.sort((a,b)=>b.score-a.score)) if (!dedup.has(c.jid)) dedup.set(c.jid, c);
  const list = Array.from(dedup.values());
  if (!list.length) throw new Error(`Grupo não encontrado: ${raw}. Peça para listar os grupos e use exatamente o nome exibido.`);
  // If many exact-looking results exist, choose the best but include name in result.
  return list[0];
}

process.on('uncaughtException', err => { log('UNCAUGHT', err?.stack || err); saveStatus({ connected: false, last_event: 'error', message: 'Erro interno na ponte WhatsApp.', error: String(err.message || err) }); });
process.on('unhandledRejection', err => { log('UNHANDLED', err?.stack || err); saveStatus({ connected: false, last_event: 'error', message: 'Erro interno na ponte WhatsApp.', error: String(err.message || err) }); });

const app = express();
app.use(cors());
app.use(express.json({ limit: '30mb' }));

app.get('/', (_req, res) => {
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.end(`<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mark-XLVIII — WhatsApp QR</title>
<style>
body{margin:0;background:#02070b;color:#8ffcff;font-family:Consolas,monospace;display:flex;min-height:100vh;align-items:center;justify-content:center}.box{width:min(92vw,520px);border:1px solid #0d5670;border-radius:14px;background:#010f18;padding:22px;box-shadow:0 0 30px #00384a}h1{color:#00d4ff;text-align:center;font-size:22px;margin:0 0 10px}.status{text-align:center;color:#d8f8ff;margin:8px 0 16px;min-height:24px}.qr{background:white;border-radius:12px;padding:10px;width:280px;height:280px;object-fit:contain;display:block;margin:0 auto}.ok{background:#001a0d;color:#00ff88;border:1px solid #00aa55;border-radius:12px;padding:35px 10px;text-align:center;font-size:28px;font-weight:bold}.muted{color:#5ab8cc;text-align:center;font-size:13px;line-height:1.45}.row{display:flex;gap:8px;justify-content:center;margin-top:16px}button{background:#011520;color:#00d4ff;border:1px solid #0d5670;border-radius:8px;padding:10px 12px;font-family:inherit;cursor:pointer}button:hover{border-color:#00d4ff}</style>
</head><body><div class="box"><h1>J.A.R.V.I.S — WhatsApp</h1><div id="status" class="status">Iniciando ponte...</div><div id="area"></div><p class="muted">No celular: WhatsApp → Aparelhos conectados → Conectar aparelho. Depois escaneie o QR Code.</p><div class="row"><button onclick="resetar()">Gerar QR novo</button><button onclick="location.reload()">Atualizar</button></div></div>
<script>
async function load(){try{const s=await fetch('/status?ts='+Date.now()).then(r=>r.json());const st=document.getElementById('status'),a=document.getElementById('area');if(s.connected){st.textContent='WhatsApp conectado com sucesso.';a.innerHTML='<div class="ok">✓<br>WHATSAPP<br>CONECTADO</div>';return;}st.textContent=s.has_qr?'Escaneie o QR Code abaixo.':(s.message||s.last_event||'Aguardando QR Code...');if(s.has_qr){a.innerHTML='<img class="qr" src="/qr?ts='+Date.now()+'">';}else{a.innerHTML='<div class="muted">QR ainda não disponível. Aguarde alguns segundos...</div>';}}catch(e){document.getElementById('status').textContent='Ponte offline ou iniciando...';document.getElementById('area').innerHTML='<div class="muted">Se demorar, rode 8_instalar_whatsapp_bridge.bat e tente novamente.</div>';}}
async function resetar(){await fetch('/reset',{method:'POST'}).catch(()=>{});setTimeout(load,1200)}
load(); setInterval(load,2500);
</script></body></html>`);
});

app.get('/status', (_req, res) => res.json(statusPayload()));
app.get('/qr', (_req, res) => { if (fs.existsSync(QR_FILE)) return res.sendFile(QR_FILE); res.status(404).json({ ok: false, error: 'QR not available.' }); });
app.post('/reset', (_req, res) => {
  log('Reset requested. Cleaning previous auth/session before new QR.');
  // Encerrar pela geracao: o fechamento desta conexao NAO pode agendar outra
  // (era isso que fazia nascer a segunda conexao, 3 s depois do reset).
  encerrarConexaoAtual();
  try { connected = false; lastQr = ''; freshLoginRequired = false; lastDisconnectReason = ''; } catch (_) {}
  try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (_) {}
  try { if (fs.existsSync(QR_FILE)) fs.unlinkSync(QR_FILE); } catch (_) {}
  saveStatus({ connected:false, has_qr:false, message:'Sessão WhatsApp antiga limpa. Gerando QR Code novo...', last_event:'reset_clean' });
  res.json({ ok: true, result: 'Sessão WhatsApp antiga limpa. Gerando QR Code novo.' });
  setTimeout(() => start({ fresh: true }), 700);
});
app.get('/contacts', (req, res) => { const q = String(req.query.q || ''); const limit = Math.min(Number(req.query.limit || 20), 50); res.json({ ok: true, query: q, contacts: searchContacts(q, limit), total_contacts: contactCount(), total_chats: chatCount(), total_groups: groupCount() }); });
app.get('/groups', async (req, res) => {
  try { if (currentConnected() && String(req.query.refresh || '1') !== '0') { try { await refreshGroups(6000); } catch(e) { log('groups refresh failed', e.message); } } const items = Object.values(groups).map(g => ({ jid: g.id, name: g.subject || g.name || g.id })).sort((a,b)=>String(a.name).localeCompare(String(b.name))); res.json({ ok: true, groups: items, total: items.length }); }
  catch(e) { res.status(500).json({ ok:false, error:String(e.message || e) }); }
});
app.get('/messages/latest', async (req, res) => {
  try {
    const limit = Math.min(Number(req.query.limit || 10), 50);
    const direction = String(req.query.direction || 'all').toLowerCase();
    const chat = String(req.query.chat || '').trim();
    const includeSystem = String(req.query.include_system || '0') === '1';
    const jid = chat ? await resolveJid(chat) : '';
    let list = messages.slice();
    if (jid) list = filterMessagesByResolvedChat(list, jid);
    if (direction === 'incoming') list = list.filter(m => !m.from_me);
    if (direction === 'outgoing' || direction === 'sent') list = list.filter(m => m.from_me);
    if (!includeSystem) list = list.filter(isMeaningfulMessageItem);
    list = list.slice(-limit).reverse();
    res.json({ ok:true, messages:list, total:messages.length });
  } catch(e) { res.status(500).json({ ok:false, error:String(e.message || e) }); }
});

app.get('/messages/audio/last', async (req, res) => {
  try {
    const chat = String(req.query.chat || '').trim();
    const jid = chat ? await resolveJid(chat) : '';
    let list = messages.slice();
    if (jid) list = filterMessagesByResolvedChat(list, jid);
    list = list.filter(m => !m.from_me && (String(m.type || '').toLowerCase().includes('audio') || String(m.media_kind || '').toLowerCase() === 'audio' || /\[áudio|\[audio/i.test(String(m.text || ''))));
    const msg = list.length ? list[list.length - 1] : null;
    res.json({ ok:true, message:msg, total:messages.length });
  } catch(e) { res.status(500).json({ ok:false, error:String(e.message || e) }); }
});

app.get('/messages/last', async (req, res) => {
  try {
    const direction = String(req.query.direction || 'all').toLowerCase();
    const chat = String(req.query.chat || '').trim();
    const includeSystem = String(req.query.include_system || '0') === '1';
    const jid = chat ? await resolveJid(chat) : '';
    let list = messages.slice();
    if (jid) list = filterMessagesByResolvedChat(list, jid);
    if (direction === 'incoming') list = list.filter(m => !m.from_me);
    if (direction === 'outgoing' || direction === 'sent') list = list.filter(m => m.from_me);
    if (!includeSystem) list = list.filter(isMeaningfulMessageItem);
    const msg = list.length ? list[list.length - 1] : null;
    res.json({ ok:true, message:msg, total:messages.length });
  } catch(e) { res.status(500).json({ ok:false, error:String(e.message || e) }); }
});
function messageStubFromPayload(payload, fallbackText = '') {
  if (payload?.text) return { conversation: String(payload.text || fallbackText || '') };
  if (payload?.image) return { imageMessage: { caption: String(payload.caption || fallbackText || '') } };
  if (payload?.video) return { videoMessage: { caption: String(payload.caption || fallbackText || '') } };
  if (payload?.audio) return { audioMessage: {} };
  if (payload?.document) return { documentMessage: { caption: String(payload.caption || fallbackText || ''), fileName: String(payload.fileName || '') } };
  return { conversation: String(fallbackText || '') };
}
async function sendAndRegister(jid, payload, timeoutMs, label, fallbackText = '') {
  if (!currentConnected()) throw new Error('WhatsApp is not connected. Click CONNECT WHATSAPP and scan the QR Code first.');
  try { await sock.presenceSubscribe(jid); } catch (_) {}
  try { await sock.sendPresenceUpdate('available', jid); } catch (_) {}
  const sent = await withTimeout(sock.sendMessage(jid, payload, { ephemeralExpiration: 0 }), timeoutMs, label);
  if (sent && sent.key) {
    // Registra exatamente a chave retornada pelo WhatsApp. Isso evita o Mark-XLVIII achar
    // que falhou e mantém a última mensagem enviada consistente para apagar/listar.
    rememberMessage({
      key: sent.key,
      message: sent.message || messageStubFromPayload(payload, fallbackText),
      messageTimestamp: sent.messageTimestamp || Math.floor(Date.now() / 1000),
      pushName: 'Você'
    });
    saveStatus({ last_event: 'message_sent', message: 'Mensagem enviada pelo WhatsApp e registrada localmente.', last_sent_key: sent.key });
  }
  return sent;
}

app.post('/send', async (req, res) => {
  try {
    if (!currentConnected()) throw new Error('WhatsApp is not connected. Click CONNECT WHATSAPP and scan the QR Code first.');
    const to = req.body.to || req.body.receiver || req.body.contact || req.body.target; const message = String(req.body.message || req.body.message_text || req.body.text || req.body.body || req.body.content || req.body.conteudo || '').trim(); const type = String(req.body.type || 'text').toLowerCase(); const audioPath = req.body.audio_path; const jid = await resolveJid(to);
    if (type === 'audio') {
      if (!audioPath || !fs.existsSync(audioPath)) throw new Error('Audio file not found.');
      const opus = await convertAudioToOpus(audioPath);
      const payload = { audio: { url: opus }, mimetype: 'audio/ogg; codecs=opus', ptt: true };
      await sendAndRegister(jid, payload, 30000, 'send audio', message || '[áudio]');
      return res.json({ ok:true, result:`Áudio/voz enviado para ${friendlyNameForJid(jid) || to} e registrado no histórico do WhatsApp.` });
    }
    if (type === 'file' || type === 'media' || type === 'image' || type === 'video' || type === 'document') {
      const filePath = req.body.file_path || req.body.path;
      const caption = String(req.body.caption || message || '');
      const payload = mediaPayload(filePath, caption);
      await sendAndRegister(jid, payload, 120000, 'send file', caption || path.basename(String(filePath || 'arquivo')));
      return res.json({ ok:true, result:`Arquivo enviado para ${friendlyNameForJid(jid) || to} e registrado no histórico do WhatsApp.` });
    }
    if (!message) throw new Error('Message text is empty.');
    await sendAndRegister(jid, { text: message }, 45000, 'send text', message);
    return res.json({ ok:true, result:`Texto enviado para ${friendlyNameForJid(jid) || to} e registrado no histórico do WhatsApp.` });
  } catch(e) { log('send error', e?.stack || e); res.status(500).json({ ok:false, error:String(e.message || e) }); }
});
app.post('/delete-last-sent', async (req, res) => {
  try {
    if (!currentConnected()) throw new Error('WhatsApp is not connected.');
    const to = String(req.body.to || '').trim();
    const requested = Math.max(1, Math.min(50, Math.floor(Number(req.body.count || req.body.limit || 1)) || 1));
    const jid = to ? await resolveJid(to) : '';
    const signature = `${jid || '*'}|${requested}`;
    const now = Date.now();

    // Uma reconexão ou chamada repetida do modelo não pode apagar o lote seguinte.
    if (recentDeleteBatch.signature === signature && now - recentDeleteBatch.at < 6000 && recentDeleteBatch.response) {
      return res.json({ ...recentDeleteBatch.response, duplicate_suppressed: true });
    }

    const candidates = messages.slice().reverse().filter(m =>
      m.from_me && m.key && !m.deleted_requested_at && (!jid || m.jid === jid)
    );
    const selected = candidates.slice(0, requested);
    if (!selected.length) {
      throw new Error(to
        ? `Não encontrei mensagem enviada para ${to} que ainda possa ser apagada no histórico local.`
        : 'Não encontrei mensagem enviada que ainda possa ser apagada no histórico local.');
    }

    const deleted = [];
    const failures = [];
    for (const msg of selected) {
      try {
        await withTimeout(sock.sendMessage(msg.jid, { delete: msg.key }), 10000, 'delete message');
        msg.deleted_requested_at = new Date().toISOString();
        deleted.push(msg);
      } catch (error) {
        failures.push({ id: msg.id || msg.key?.id || '', error: String(error?.message || error) });
      }
    }
    if (deleted.length) saveDb();
    if (!deleted.length) throw new Error(`Não consegui apagar as ${selected.length} mensagens encontradas.`);

    const targetText = to ? ` para ${to}` : '';
    let result = `Solicitei apagar ${deleted.length} ${deleted.length === 1 ? 'mensagem enviada' : 'mensagens enviadas'}${targetText}.`;
    if (selected.length < requested) {
      result += ` Encontrei somente ${selected.length} que ainda não tinham sido apagadas.`;
    }
    if (failures.length) {
      result += ` ${failures.length} ${failures.length === 1 ? 'mensagem falhou' : 'mensagens falharam'} durante a exclusão.`;
    }
    const response = {
      ok: true,
      result,
      requested_count: requested,
      found_count: selected.length,
      deleted_count: deleted.length,
      failed_count: failures.length,
      messages: deleted,
      failures,
    };
    recentDeleteBatch = { signature, at: Date.now(), response };
    res.json(response);
  }
  catch(e) { log('delete error', e?.stack || e); res.status(500).json({ ok:false, error:String(e.message || e) }); }
});
app.post('/alias', async (req, res) => {
  try {
    const name = req.body.name || req.body.alias || req.body.contact || '';
    const phone = req.body.phone || req.body.number || req.body.jid || '';
    const a = saveAlias(name, phone);
    res.json({ ok: true, result: `Contato salvo: ${a.name} -> ${a.jid}`, alias: a });
  } catch(e) { res.status(500).json({ ok:false, error:String(e.message || e) }); }
});
app.get('/aliases', (_req, res) => { res.json({ ok:true, aliases:Object.values(aliases || {}), total:Object.keys(aliases || {}).length }); });
app.post('/auto-alias-from-messages', (_req, res) => {
  let count = 0;
  for (const m of (messages || [])) {
    if (m?.is_group || isGroupJid(m?.jid)) continue;
    const before = Object.keys(aliases).length;
    autoAliasFromName(m.name, m.sender_jid || m.jid, 'private_history');
    autoAliasFromName(m.chat_name, m.jid, 'private_history');
    if (Object.keys(aliases).length > before) count++;
  }
  saveDb();
  res.json({ ok:true, result:`Aliases automáticos atualizados a partir das mensagens: ${count}`, aliases:Object.values(aliases || {}), total:Object.keys(aliases || {}).length });
});

app.post('/groups/leave', async (req, res) => {
  try {
    if (!currentConnected()) throw new Error('WhatsApp is not connected.');
    const to = String(req.body.to || req.body.group || req.body.name || '').trim();
    const confirmRaw = req.body.confirm;
    const confirm = confirmRaw === true || String(confirmRaw || '').toLowerCase() === 'true' || String(confirmRaw || '').toLowerCase() === 'sim' || String(confirmRaw || '').toLowerCase() === 'confirmo';
    const g = await resolveGroupJid(to);
    if (!String(g.jid || '').endsWith('@g.us')) throw new Error('O destino encontrado não é um grupo do WhatsApp.');
    if (!confirm) {
      pendingLeaveGroup = { jid: g.jid, name: g.name, requested_at: new Date().toISOString() };
      saveDb();
      saveStatus({ last_event:'leave_group_pending', message:`Confirmação pendente para sair do grupo ${g.name}.` });
      return res.json({ ok:true, needs_confirmation:true, group:g, result:`Confirma que deseja sair do grupo "${g.name}"? Responda confirmando para eu sair.` });
    }
    await withTimeout(sock.groupLeave(g.jid), 15000, 'groupLeave');
    delete groups[g.jid];
    delete chats[g.jid];
    pendingLeaveGroup = null;
    saveDb();
    saveStatus({ last_event:'left_group', message:`Sai do grupo ${g.name}.` });
    return res.json({ ok:true, result:`Saí do grupo "${g.name}" com sucesso.`, group:g });
  } catch(e) { log('leave group error', e?.stack || e); res.status(500).json({ ok:false, error:String(e.message || e) }); }
});


app.post('/alerts/watch', async (req, res) => {
  try {
    let mode = String(req.body.mode || req.body.action || '').toLowerCase().trim();
    const contact = String(req.body.contact || req.body.to || req.body.name || req.body.group || '').trim();
    let result = '';
    if (['off','stop','disable','desativar','parar'].includes(mode)) result = stopWatch();
    else if (['private','privado','privados','contatos','somente_privado','somente_privados','contacts_only'].includes(mode)) result = setWatchPrivateOnly();
    else if (['groups','group_only','grupos','somente_grupos','grupo_todos'].includes(mode)) result = setWatchGroupsOnly();
    else if (['group','grupo','specific_group','grupo_especifico'].includes(mode)) result = setWatchGroup(contact);
    else if (['contact','contacts','contato','specific_contact','contato_especifico'].includes(mode)) result = setWatchContact(contact);
    else if (contact) {
      // If user says "me avise sobre o grupo X", route to group; otherwise contact.
      if (mode.includes('grupo')) result = setWatchGroup(contact); else result = setWatchContact(contact);
    }
    else result = setWatchAny();
    saveStatus({ last_event:'watch_messages', message:result });
    res.json({ ok:true, result, watch:watchConfig });
  } catch(e) { res.status(500).json({ ok:false, error:String(e.message || e) }); }
});
app.get('/alerts/status', (_req, res) => res.json({ ok:true, watch:watchConfig, queue:alertQueue.length }));
app.get('/alerts/poll', (req, res) => {
  const limit = Math.min(Number(req.query.limit || 20), 100);
  const items = alertQueue.splice(0, limit);
  saveDb();
  res.json({ ok:true, alerts:items, total:items.length, watch:watchConfig });
});


app.get('/agent/poll', (req, res) => {
  const limit = Math.min(Number(req.query.limit || 20), 100);
  const items = agentQueue.splice(0, limit);
  // Fallback: entrega também mensagens recentes que podem ter ficado fora da fila.
  // O Python deduplica e filtra pelo horário de ativação do agente.
  try {
    const recent = (messages || []).slice(-80).filter(m => m && !m.from_me && isMeaningfulMessageItem(m));
    for (const m of recent) {
      if (items.length >= limit) break;
      const isGroup = Boolean(m.is_group || String(m.jid || '').endsWith('@g.us'));
      items.push({
        id: `${m.jid || ''}:${m.sender_jid || ''}:${m.id || m.timestamp || ''}`,
        name: m.name || m.sender_name || 'contato',
        chat_name: m.chat_name || m.name || m.jid || 'conversa',
        jid: m.jid,
        sender_jid: m.sender_jid,
        text: String(m.text || '').slice(0, 1200),
        is_group: isGroup,
        chat_type: isGroup ? 'group' : 'private',
        type: m.type || 'message',
        media_kind: m.media_kind || '',
        media_path: m.media_path || '',
        timestamp: m.timestamp || Math.floor(Date.now()/1000),
        created_at: new Date().toISOString(),
      });
    }
  } catch (_) {}
  saveDb();
  res.json({ ok:true, messages:items, total:items.length });
});

app.post('/logout', async (_req, res) => {
  log('Manual WhatsApp logout requested. Closing linked device and local session.');
  manualLogoutInProgress = true;
  let logoutError = '';
  try {
    if (sock && currentConnected()) {
      await withTimeout(sock.logout(), 20000, 'logout');
    }
  } catch (e) {
    logoutError = String(e.message || e);
    log('logout command failed/timeout; cleaning local session anyway', logoutError);
  }
  encerrarConexaoAtual();
  try { connected = false; lastQr = ''; freshLoginRequired = true; lastDisconnectReason = 'user_logged_out'; lastEvent = 'manual_logout'; } catch (_) {}
  try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (_) {}
  try { if (fs.existsSync(QR_FILE)) fs.unlinkSync(QR_FILE); } catch (_) {}
  try { if (fs.existsSync(STATUS_FILE)) fs.unlinkSync(STATUS_FILE); } catch (_) {}
  pendingLeaveGroup = null;
  saveDb();
  const msg = logoutError
    ? `WhatsApp desconectado localmente e sessão limpa. Aviso: o comando de logout real retornou: ${logoutError}. Se ainda aparecer no celular, remova em WhatsApp > Aparelhos conectados.`
    : 'WhatsApp desconectado completamente. Sessão encerrada no Jarvis e no aparelho conectado; será necessário escanear um novo QR Code.';
  saveStatus({ connected:false, has_qr:false, message:msg, disconnect_reason:lastDisconnectReason, fresh_login_required:true, last_event:'manual_logout' });
  res.json({ ok:true, result: msg, real_logout: !logoutError, warning: logoutError });
});
app.post('/disconnect', async (req, res) => {
  req.url = '/logout';
  return app._router.handle(req, res);
});
app.listen(PORT, '127.0.0.1', () => { log(`Bridge running at http://127.0.0.1:${PORT}`); saveStatus({ connected:false, has_qr:false, message:'Servidor local WhatsApp iniciado. Preparando QR Code...', last_event:'server_listening' }); start({ fresh: false }); });
setInterval(() => {
  try {
    const cc = currentConnected();
    const msg = cc ? 'WhatsApp conectado com sucesso.' : (lastDisconnectReason === 'user_logged_out' ? 'WhatsApp desconectado completamente. Sessão antiga limpa; peça conectar WhatsApp para gerar QR novo.' : 'Ponte WhatsApp ativa. Aguardando conexão/QR Code.');
    saveStatus({ connected: cc, has_qr: Boolean(lastQr && !cc && fs.existsSync(QR_FILE)), message: msg, disconnect_reason: lastDisconnectReason, last_event: cc ? 'connected' : lastEvent });
  } catch (_) {}
}, 2500);
