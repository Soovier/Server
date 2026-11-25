// Probate outreach copy. Written to be respectful: these people just lost someone.
// Placeholders are filled from the lead and your settings.

function fill(template, vars) {
  return template.replace(/\{(\w+)\}/g, (_, k) => (vars[k] != null && vars[k] !== '' ? vars[k] : `{${k}}`));
}

function varsFor(lead, settings = {}) {
  const contact = lead.contactName || lead.ownerName || 'there';
  return {
    first: contact.split(/\s+/)[0],
    contact,
    decedent: lead.ownerName || 'your loved one',
    address: lead.address,
    city: lead.city,
    myName: settings.myName || '{myName}',
    company: settings.company || '{company}',
    myPhone: settings.myPhone || '{myPhone}',
    bookingLink: settings.bookingLink || '{bookingLink}',
  };
}

const TEMPLATES = {
  // First touch for probate is a letter to the personal representative. Lowest legal risk, best response.
  letter: `Dear {contact},

I'm sorry for your loss. I'm reaching out because I understand you may be handling the estate of {decedent}, including the property at {address}.

Settling an estate is a lot of work, and a house can be the hardest part: repairs, clean-out, taxes, insurance, and coordinating with family. I buy houses as-is, for cash, and I can work around the probate timeline. You wouldn't need to clean, repair, or list it, and there are no agent commissions.

If selling the house would make things easier, call or text me at {myPhone}, or pick a time that suits you here: {bookingLink}. If not, I completely understand and won't bother you again.

With sympathy,
{myName}
{company}`,

  sms_first: `Hi {first}, this is {myName} with {company}. I'm sorry for your loss. I buy houses as-is for cash and wanted to ask if you'd consider an offer on {address}? Reply STOP to opt out.`,

  sms_followup: `Hi {first}, {myName} again. No pressure at all. If it would help to get a cash number on {address} while you sort out the estate, I'm happy to send one. Reply STOP to opt out.`,

  email_followup: `Hi {first},

Just following up on {address}. If an as-is cash sale would take something off your plate, I can give you a number within 24 hours, and we can close whenever the court allows. Grab a time here: {bookingLink}

{myName}
{company} | {myPhone}`,
};

// Talk track for YOUR calls. Shown next to the deal numbers on the call list.
const CALL_SCRIPT = [
  'Open: "Hi {first}, this is {myName}. I sent you a letter about {address}. First, I\'m sorry for your loss. Is now an OK time?"',
  'Situation: "How are things going with the estate? Has the court appointed you as the personal representative yet?"',
  'House: "What shape is the house in? Is anyone living there? Anything that needs work: roof, HVAC, foundation?"',
  'Family: "Is anyone else involved in the decision, like other heirs?"',
  'Timeline: "Ideally, when would you want this wrapped up?"',
  'Price: "Do you have a number in mind that would make this worth it for you?" (Let them go first. Your max offer is on the call sheet.)',
  'Next step: "Based on what you\'ve told me I can likely be around {openingOffer}. I can send a simple agreement today, and you pick the closing date."',
];

module.exports = { TEMPLATES, CALL_SCRIPT, fill, varsFor };
