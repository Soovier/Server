// Your details, used in letters, texts and contracts. Set them as environment variables
// (or edit the defaults here). Leaving one blank keeps a {placeholder} in the draft so you notice.
module.exports = {
  myName: process.env.DEAL_MY_NAME || '',
  company: process.env.DEAL_COMPANY || '',
  myPhone: process.env.DEAL_MY_PHONE || '',
  bookingLink: process.env.DEAL_BOOKING_LINK || '', // e.g. a Google Calendar appointment schedule link
  buyerEntity: process.env.DEAL_BUYER_ENTITY || '', // your LLC name
  titleCompany: process.env.DEAL_TITLE_COMPANY || '',
  deal: {
    buyerPct: Number(process.env.DEAL_BUYER_PCT || 0.70),
    targetFee: Number(process.env.DEAL_TARGET_FEE || 10000),
    minFee: Number(process.env.DEAL_MIN_FEE || 5000),
  },
};
