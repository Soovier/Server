// Provider adapters. Each returns null when its keys are missing, so the engine stays in dry-run.
// Keys come from environment variables only; never commit them.

function twilioSms() {
  const { TWILIO_ACCOUNT_SID: sid, TWILIO_AUTH_TOKEN: token, TWILIO_FROM_NUMBER: from } = process.env;
  if (!sid || !token || !from) return null;
  return async (item) => {
    const res = await fetch(`https://api.twilio.com/2010-04-01/Accounts/${sid}/Messages.json`, {
      method: 'POST',
      headers: {
        Authorization: `Basic ${Buffer.from(`${sid}:${token}`).toString('base64')}`,
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: new URLSearchParams({ To: item.to, From: from, Body: item.body }),
    });
    const json = await res.json();
    if (!res.ok) throw new Error(`Twilio ${res.status}: ${json.message || 'error'}`);
    return { sid: json.sid };
  };
}

// ElevenLabs conversational agent placing a call through your Twilio number.
// Only used for leads with consent (sellers who called or texted in and asked for a call back).
function elevenLabsCall() {
  const { ELEVENLABS_API_KEY: key, ELEVENLABS_AGENT_ID: agentId, ELEVENLABS_PHONE_NUMBER_ID: phoneId } = process.env;
  if (!key || !agentId || !phoneId) return null;
  return async (item) => {
    const res = await fetch('https://api.elevenlabs.io/v1/convai/twilio/outbound-call', {
      method: 'POST',
      headers: { 'xi-api-key': key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId, agent_phone_number_id: phoneId, to_number: item.to }),
    });
    const json = await res.json();
    if (!res.ok) throw new Error(`ElevenLabs ${res.status}: ${JSON.stringify(json.detail || json)}`);
    return { conversationId: json.conversation_id, callSid: json.callSid };
  };
}

function senders() {
  return { sms: twilioSms(), ai_call: elevenLabsCall() };
}

module.exports = { senders, twilioSms, elevenLabsCall };
