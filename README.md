# Chat App (Phase 1: Text + Voice Notes + File Sharing)

Ye ek real-time chat website hai. Link share karne se doosra insaan seedha
usi chat room mein aa jata hai.

## Abhi Kya Kaam Karta Hai
- ✅ Real-time text messages
- ✅ Voice note record kar ke bhejna
- ✅ Image/video/file sharing
- ✅ Link se koi bhi chat mein shamil ho sakta hai

## Abhi Kya Baaki Hai (Phase 2)
- ⏳ Voice call
- ⏳ Video call
- ⏳ Login/account system (abhi koi bhi guest ke tor par aa sakta hai)

Voice/video call ke liye WebRTC technology chahiye hoti hai, jo isse
zyada complex hai — ye agla phase hoga.

---

## Kaise Chalayein

```bash
pip install -r requirements.txt
python app.py
```

Browser mein kholein: **http://127.0.0.1:5000**

1. "Naya Chat Shuru Karein" par click karein
2. Upar "Link Copy Karein" button dabayein
3. Wo link WhatsApp/kahin bhi doosre insaan ko bhej dein
4. Jab wo link kholega, seedha isi chat room mein aa jayega
5. Dono log ab real-time chat kar sakte hain

## Note
Ye abhi sirf aapke apne computer (local network) par chalti hai. Agar
dusra insaan kisi doosri jagah (dusre wifi/internet) se access karna
chahe, to app ko internet par host karna parega (jaise Render.com,
Railway.app, ya PythonAnywhere — ye sab free plans dete hain).
