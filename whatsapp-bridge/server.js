// Local WhatsApp bridge for Olive Hub. Do NOT expose outside localhost.
// Uses whatsapp-web.js (unofficial) — scan the QR once with the phone's
// WhatsApp app: Settings > Linked devices > Link a device.
const path = require('path');
const fs = require('fs');
const express = require('express');
const qrcode = require('qrcode');
const { Client, LocalAuth } = require('whatsapp-web.js');

const ROOT = __dirname;
const SESSION = path.join(ROOT, process.env.BRIDGE_SESSION_DIR || 'session');
const QR_PNG = path.join(ROOT, 'qr.png');
const PORT = Number(process.env.BRIDGE_PORT || 3011);
const TOKEN = String(process.env.BRIDGE_TOKEN || '');
const CHROME = process.env.CHROME_PATH ||
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

if (!TOKEN) {
  console.error('BRIDGE_TOKEN is not set; refusing to start');
  process.exit(1);
}
fs.mkdirSync(SESSION, { recursive: true });

const state = { status: 'starting', lastError: '', qrAt: 0, sent: 0 };
let contactsCache = { at: 0, list: [] };

async function getContacts() {
  if (Date.now() - contactsCache.at < 10 * 60 * 1000 && contactsCache.list.length) {
    return contactsCache.list;
  }
  const all = await client.getContacts();
  contactsCache = {
    at: Date.now(),
    list: all.map((c) => ({
      name: c.name || '',
      number: c.number || '',
      id: c.id._serialized,
      isGroup: !!c.isGroup,
    })),
  };
  return contactsCache.list;
}

const client = new Client({
  authStrategy: new LocalAuth({ clientId: 'olive-hub', dataPath: SESSION }),
  puppeteer: {
    executablePath: CHROME,
    headless: process.env.BRIDGE_HEADLESS !== '0',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
           '--no-proxy-server'],
  },
});

client.on('loading_screen', (percent, message) => {
  console.log(`[bridge] loading ${percent}% ${message || ''}`);
});
client.on('qr', async (qr) => {
  try {
    await qrcode.toFile(QR_PNG, qr, { width: 512, margin: 2 });
    // Self-contained preview page: the Review browser cannot reach localhost,
    // so embed the QR as a data URI and reload the file every 5 seconds.
    const dataUrl = await qrcode.toDataURL(qr, { width: 512, margin: 2 });
    const html = `<!doctype html><html><head><meta charset="utf-8">` +
      `<meta http-equiv="refresh" content="5">` +
      `<style>body{font-family:system-ui;text-align:center;padding:20px}` +
      `img{width:420px;height:420px;image-rendering:pixelated}</style></head><body>` +
      `<h2>Scan with WhatsApp</h2>` +
      `<p>Settings → Linked devices → Link a device</p>` +
      `<img src="${dataUrl}"><p>Refreshes automatically.</p></body></html>`;
    fs.writeFileSync(path.join(ROOT, 'qr-live.html'), html);
    state.qrAt = Date.now();
    state.status = 'qr';
    console.log('[bridge] QR written to ' + QR_PNG);
  } catch (e) {
    state.lastError = String(e);
    console.error('[bridge] QR write failed', e);
  }
});
client.on('ready', () => {
  state.status = 'ready';
  state.lastError = '';
  console.log('[bridge] WhatsApp ready');
});
client.on('authenticated', () => {
  // With a persisted LocalAuth session this means the client is usable
  // (contacts and sends work even if 'ready' arrives late or not at all).
  state.status = 'ready';
  console.log('[bridge] authenticated -> ready');
});
client.on('auth_failure', (msg) => {
  state.status = 'auth_failure';
  state.lastError = String(msg);
  console.error('[bridge] auth failure', msg);
});
client.on('disconnected', (reason) => {
  state.status = 'disconnected';
  state.lastError = String(reason);
  console.warn('[bridge] disconnected', reason);
});

const app = express();
app.use(express.json({ limit: '64kb' }));
app.use((req, res, next) => {
  if (req.path !== '/health' && req.headers['x-bridge-token'] !== TOKEN) {
    return res.status(401).json({ ok: false, error: 'unauthorized' });
  }
  next();
});

app.get('/health', (req, res) => {
  res.json({ ok: true, status: state.status, lastError: state.lastError,
             qrAvailable: fs.existsSync(QR_PNG), sent: state.sent });
});

app.get('/qr', (req, res) => {
  if (!fs.existsSync(QR_PNG)) return res.status(404).json({ ok: false, error: 'no QR yet' });
  res.sendFile(QR_PNG);
});

app.get('/contacts', async (req, res) => {
  try {
    const q = String(req.query.q || '').toLowerCase().trim();
    const contacts = await getContacts();
    const matches = contacts
      .filter((c) => !c.isGroup && (c.name || c.number || ''))
      .filter((c) => !q || c.name.toLowerCase().includes(q) || c.number.includes(q))
      .slice(0, 25);
    res.json({ ok: true, contacts: matches });
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e) });
  }
});

app.post('/send', async (req, res) => {
  const to = String(req.body.to || '').trim();
  const text = String(req.body.text || '').trim();
  if (!to || !text) return res.status(400).json({ ok: false, error: 'to and text required' });
  if (state.status !== 'ready') {
    return res.status(503).json({ ok: false, error: 'WhatsApp not ready: ' + state.status });
  }
  try {
    let chatId = to;
    if (/^\+?\d[\d\s().-]{5,}$/.test(to)) {
      // E.164 phone number: send directly instead of searching contacts.
      chatId = to.replace(/[^\d]/g, '') + '@c.us';
    } else if (!to.includes('@')) {
      const contacts = await getContacts();
      const needle = to.toLowerCase();
      const match = contacts.find((c) => !c.isGroup &&
        ((c.name || '').toLowerCase() === needle ||
         (c.name || '').toLowerCase().includes(needle) ||
         (c.number || '').includes(needle)));
      if (!match) return res.status(404).json({ ok: false, error: 'contact not found: ' + to });
      chatId = match.id._serialized;
    }
    const message = await client.sendMessage(chatId, text);
    state.sent += 1;
    res.json({ ok: true, id: message.id._serialized, to: chatId });
  } catch (e) {
    state.lastError = String(e);
    res.status(500).json({ ok: false, error: String(e) });
  }
});

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[bridge] listening on http://127.0.0.1:${PORT}`);
  client.initialize().catch((e) => {
    state.lastError = String(e);
    console.error('[bridge] initialize failed', e);
  });
});
