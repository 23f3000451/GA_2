const express = require('express');
const cors = require('cors');

const API_KEY = 'ak_vxymeuol88bj442gkxsy9ev4';
const EMAIL = '23f3000451@ds.study.iitm.ac.in';

const app = express();
app.use(cors());
app.use(express.json());

app.post('/', (req, res) => {
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

  res.json({
    email: EMAIL,
    total_events: events.length,
    unique_users: userSet.size,
    revenue: Math.round(revenue * 100) / 100,
    top_user
  });
});

const PORT = process.env.PORT || 10000;
app.listen(PORT, '0.0.0.0', () => {
  console.log(`Server listening on 0.0.0.0:${PORT}`);
});
