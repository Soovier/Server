// Creates conversational AI agents through the ElevenLabs REST API.
// Docs: https://elevenlabs.io/docs/api-reference/agents/create
import { config, requireKey } from "./config.mjs";
import { firstMessage, receptionistPrompt } from "./prompt.mjs";

export function agentPayload(biz, { demo = true, transferNumber = "", voiceId = config.voiceId } = {}) {
  const payload = {
    name: `${demo ? "[DEMO] " : ""}${biz.name} receptionist`,
    tags: [demo ? "demo" : "client", biz.trade || "service"].filter(Boolean),
    conversation_config: {
      agent: {
        first_message: firstMessage(biz, { demo }),
        language: "en",
        prompt: {
          prompt: receptionistPrompt(biz, { demo, transferNumber }),
          llm: "gemini-3.5-flash",
          temperature: 0.3,
        },
      },
      conversation: { max_duration_seconds: demo ? 300 : 900 },
    },
  };
  if (voiceId) payload.conversation_config.tts = { voice_id: voiceId };
  return payload;
}

export async function createAgent(biz, opts = {}, fetchImpl = fetch) {
  const key = requireKey("ELEVENLABS_API_KEY", config.elevenLabsApiKey);
  const res = await fetchImpl("https://api.elevenlabs.io/v1/convai/agents/create", {
    method: "POST",
    headers: { "Content-Type": "application/json", "xi-api-key": key },
    body: JSON.stringify(agentPayload(biz, opts)),
  });
  if (!res.ok) throw new Error(`ElevenLabs ${res.status}: ${await res.text()}`);
  const { agent_id } = await res.json();
  return { agentId: agent_id, talkUrl: talkUrl(agent_id) };
}

export function talkUrl(agentId) {
  return `https://elevenlabs.io/app/talk-to?agent_id=${encodeURIComponent(agentId)}`;
}
