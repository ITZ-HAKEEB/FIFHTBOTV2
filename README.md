# 🎵 Audio Controller v7 - Ultra Premium

Extra Extreme Power + Unlimited Echo & Advanced Audio Processing with ZERO-LATENCY for Telegram Voice Chats.

---

## 🚀 One-Click Deploy on Heroku

Heroku पर सिंगल क्लिक में डिप्लॉय करने के लिए नीचे दिए गए बटन पर क्लिक करें:

[![Deploy to Heroku](https://www.herokuapp.com/deploy/desktop_app.svg)](https://heroku.com/deploy)

---

## 📌 Features

- ⚡ **Zero-Latency Audio Forwarding**: वॉयस चैट से ऑडियो को बिना किसी डिले के दूसरे ग्रुप्स में लाइव फॉरवर्ड करें।
- 🎛️ **Unlimited Effects**: Volume, Bass Boost, Treble Boost, Gain Control, Overdrive/Distortion और Unlimited Echo.
- 🔄 **Dynamic Client Engine**: अलग-अलग चैट्स के लिए डायनामिक यूजरबॉट इंटीग्रेशन।
- 🛡️ **Owner & User Authorization**: केवल अप्रूव्ड यूजर्स ही बॉट कमांड्स का इस्तेमाल कर सकते हैं।
- 📊 **Interactive Control Panel**: इनलाइन बटन के साथ आसान कंट्रोल डैशबोर्ड।

---

## 🛠️ Required Environment Variables (Config Vars)

डिप्लॉय करते समय आपको Heroku पर निम्नलिखित वेरिएबल्स (Variables) भरने होंगे:

| Variable | Description |
| :--- | :--- |
| `API_ID` | Telegram API ID (my.telegram.org से प्राप्त करें) |
| `API_HASH` | Telegram API Hash |
| `BOT_TOKEN` | Bot Token (@BotFather से प्राप्त करें) |
| `OWNER_ID` | Owner का Telegram User ID |
| `STRING_SESSION` | Pyrogram V2 String Session (Userbot के लिए) |
| `RECORD_GROUP` | डिफ़ॉल्ट सोर्स ग्रुप आईडी (जहां से ऑडियो रिकॉर्ड होगा) |

---

## 🎮 Essential Commands

- `/start` - बॉट शुरू करें
- `/help` - सभी कमांड्स की सूची देखें
- `/record` - सोर्स ग्रुप से ऑडियो रिकॉर्डिंग शुरू करें
- `/join <chat_id>` - किसी ग्रुप की वॉयस चैट में ऑडियो फॉरवर्ड करें
- `/rejoin` - सभी चैट्स को रीकनेक्ट करें
- `/mute` / `/unmute` - ऑडियो फॉरवर्डिंग म्यूट या अनम्यूट करें
- `/level <value>` - वॉल्यूम चेंज करें (No Limits)
- `/bass <value>` - बास बूस्ट सेट करें
- `/treble <value>` - ट्रेबल बूस्ट सेट करें
- `/overdrive <value>` - डिस्टॉर्शन/ओवरड्राइव सेट करें
- `/echo delay <ms>` - इको डिले सेट करें
- `/effects` - एक्टिव ऑडियो इफेक्ट्स देखें
- `/reset` - सभी इफेक्ट्स रिसेट करें
- `/panel` - इनलाइन कंट्रोल पैनल खोलें
- `/approve <user_id>` - किसी यूजर को अप्रूव करें (Owner Only)
- `/setrecordgroup <chat_id>` - सोर्स ग्रुप आईडी बदलें (Owner Only)

---

## 📄 License
This project is open-source and free to use.
