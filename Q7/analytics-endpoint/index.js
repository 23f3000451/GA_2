const API_KEY = 'ak_vxymeuol88bj442gkxsy9ev4';
const EMAIL = '23f3000451@ds.study.iitm.ac.in';

module.exports = (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-API-Key');
  
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }
  
  const apiKey = req.headers['x-api-key'];
  if (!apiKey || apiKey !== API_KEY) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  
  const events = req.body?.events || [];
  const userSet = new Set();
  const positiveTotals = {};
  let revenue = 0;
  
  for (const event of events) {
    if (event.user !== undefined) userSet.add(event.user);
    if (typeof event.amount === 'number' && event.amount > 0) {
      revenue += event.amount;
      positiveTotals[event.user] = (positiveTotals[event.user] || 0) + event.amount;
    }
  }
  
  let top_user = null;
  let maxTotal = -Infinity;
  for (const [user, total] of Object.entries(positiveTotals)) {
    if (total > maxTotal) {
      maxTotal = total;
      top_user = user;
    }
  }
  if (top_user === null && userSet.size > 0) {
    top_user = userSet.values().next().value;
  }
  
  return res.json({
    email: EMAIL,
    total_events: events.length,
    unique_users: userSet.size,
    revenue: Math.round(revenue * 100) / 100,
    top_user
  });
};
