// Outreach batches. The system drafts and checks every message; nothing is sent until a person
// approves the batch. Even then, senders run in dry-run mode until DEAL_ENGINE_LIVE=1 and the
// provider keys are set.
const crypto = require('node:crypto');
const { checkRecipient } = require('./compliance');
const { TEMPLATES, fill, varsFor } = require('./messages');

const CHANNEL_TEMPLATE = { sms: 'sms_first', sms_followup: 'sms_followup', email: 'email_followup', mail: 'letter' };

function draftBatch(store, { leadIds, channel, template, settings = {}, now = new Date() }) {
  const dnc = new Set(store.data.dnc || []);
  const optOuts = new Set(store.data.optOuts);
  const tplName = template || CHANNEL_TEMPLATE[channel];
  const baseChannel = channel === 'sms_followup' ? 'sms' : channel;
  const items = [];
  const excluded = [];
  for (const id of leadIds) {
    const lead = store.lead(id);
    if (!lead) continue;
    const phone = lead.phones && lead.phones[0];
    const check = checkRecipient({ lead, phone, channel: baseChannel, dnc, optOuts, now });
    if (!check.ok) { excluded.push({ leadId: id, address: lead.address, reasons: check.blocks }); continue; }
    const body = tplName ? fill(TEMPLATES[tplName], varsFor(lead, settings)) : null;
    items.push({
      leadId: id,
      to: baseChannel === 'email' ? lead.email : baseChannel === 'mail' ? (lead.contactMailing || lead.mailingAddress || lead.address) : phone,
      name: lead.contactName || lead.ownerName,
      address: lead.address,
      body,
      warnings: check.warnings,
      status: 'drafted',
    });
  }
  const batch = {
    id: crypto.randomUUID().slice(0, 8),
    channel: baseChannel,
    template: tplName,
    createdAt: now.toISOString(),
    status: items.length ? 'pending_approval' : 'empty',
    items,
    excluded,
  };
  store.data.batches.push(batch);
  store.log('batch_drafted', { batch: batch.id, channel: baseChannel, count: items.length, excluded: excluded.length });
  return batch;
}

function approveBatch(store, batchId, approvedBy) {
  const batch = store.batch(batchId);
  if (!batch) throw new Error(`No batch ${batchId}`);
  if (batch.status !== 'pending_approval') throw new Error(`Batch ${batchId} is ${batch.status}, not pending approval`);
  if (!approvedBy) throw new Error('Approval needs a name');
  batch.status = 'approved';
  batch.approvedBy = approvedBy;
  batch.approvedAt = new Date().toISOString();
  store.log('batch_approved', { batch: batchId, by: approvedBy });
  return batch;
}

function rejectBatch(store, batchId, by) {
  const batch = store.batch(batchId);
  if (!batch) throw new Error(`No batch ${batchId}`);
  batch.status = 'rejected';
  store.log('batch_rejected', { batch: batchId, by });
  return batch;
}

/**
 * Sends an approved batch. Re-checks compliance at send time (hours may have passed since approval).
 * `senders` maps channel -> async (item) => providerResult. Without DEAL_ENGINE_LIVE=1 nothing leaves.
 */
async function sendBatch(store, batchId, senders, { live = process.env.DEAL_ENGINE_LIVE === '1', now = new Date() } = {}) {
  const batch = store.batch(batchId);
  if (!batch) throw new Error(`No batch ${batchId}`);
  if (batch.status !== 'approved') throw new Error(`Batch ${batchId} is not approved (status: ${batch.status})`);
  const send = senders[batch.channel];
  const dnc = new Set(store.data.dnc || []);
  const optOuts = new Set(store.data.optOuts);
  for (const item of batch.items) {
    if (item.status === 'sent') continue;
    const lead = store.lead(item.leadId);
    const check = checkRecipient({ lead, phone: item.to, channel: batch.channel, dnc, optOuts, now });
    if (!check.ok) { item.status = 'held'; item.heldFor = check.blocks; continue; }
    if (batch.channel === 'mail') {
      // Letters are printed and mailed by you (or uploaded to a mail service). Approval = ready to print.
      item.status = 'to_print';
      if (lead.stage === 'new') store.setStage(lead.id, 'contacted', `letter approved in batch ${batch.id}`);
      continue;
    }
    if (!live || !send) { item.status = 'dry_run'; continue; }
    try {
      item.result = await send(item);
      item.status = 'sent';
      item.sentAt = new Date().toISOString();
      if (lead.stage === 'new') store.setStage(lead.id, 'contacted', `${batch.channel} via batch ${batch.id}`);
    } catch (e) {
      item.status = 'failed';
      item.error = String(e.message || e);
    }
  }
  const counts = batch.items.reduce((acc, i) => ({ ...acc, [i.status]: (acc[i.status] || 0) + 1 }), {});
  batch.status = counts.sent ? 'sent' : counts.to_print ? 'to_print' : 'approved';
  batch.lastRun = { at: new Date().toISOString(), live, counts };
  store.log('batch_send', { batch: batch.id, live, counts });
  return batch;
}

module.exports = { draftBatch, approveBatch, rejectBatch, sendBatch };
