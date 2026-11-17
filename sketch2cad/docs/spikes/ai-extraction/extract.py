"""Spike: ask a vision model to read a hand-drawn floor plan and return structured data.
Run: uv run --no-project --with anthropic --with pillow python extract.py <image> <out.json>
The API key is read from ANTHROPIC_API_KEY and never printed or stored."""
import base64, io, json, sys, time
import anthropic
from PIL import Image

MODEL = "claude-opus-5-5"
PRICE_IN, PRICE_OUT = 4.00 / 1e6, 20.00 / 1e6   # cached skill table (2026-10-06): verify

PROMPT = """You are reading a hand-drawn floor plan sketch (field sketch, NOT to scale).
Return ONLY a JSON object (no prose, no code fence) with this shape:
{
 "dimensions": [ {"text_as_written": str, "value_m": number|null, "unit_note": str,
                  "refers_to": str, "bbox": [x0,y0,x1,y1], "legible": "clear"|"uncertain"|"illegible"} ],
 "rooms": [ {"label": str|null, "bbox": [x0,y0,x1,y1]} ],
 "walls": [ {"points": [[x,y],[x,y]...], "kind": "exterior"|"interior"|"partition"|"other"} ],
 "openings": [ {"kind": "door"|"window"|"other", "bbox": [x0,y0,x1,y1]} ],
 "notes": str
}
Rules: all coordinates are NORMALIZED to the image (0..1, origin top-left, x right, y down).
List EVERY written dimension you can see, exactly as written, then convert to meters in value_m
(a bare number like 60 is probably centimetres: say so in unit_note; "4 m" is 4.0 m).
If a number is crossed out, overwritten or ambiguous mark legible "uncertain" and still give your best reading.
Do not invent dimensions that are not written. Walls: only the main wall lines, ortho-snapped is fine."""

def prep(path, max_side=2000):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    s = min(1.0, max_side / max(w, h))
    if s < 1:
        im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
    return base64.standard_b64encode(buf.getvalue()).decode(), im.size

def main(img, out):
    data, size = prep(img)
    client = anthropic.Anthropic()
    t0 = time.time()
    with client.messages.stream(
        model=MODEL, max_tokens=16000,
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
            {"type": "text", "text": PROMPT}]}],
    ) as stream:
        msg = stream.get_final_message()
    dt = time.time() - t0
    text = "".join(b.text for b in msg.content if b.type == "text")
    u = msg.usage
    cost = u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT
    rec = {"image": img, "sent_size": size, "model": MODEL, "stop_reason": msg.stop_reason,
           "seconds": round(dt, 1), "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
           "cost_usd_estimate": round(cost, 4), "raw_text": text}
    try:
        rec["parsed"] = json.loads(text)
    except Exception as e:  # keep the raw text for inspection
        rec["parse_error"] = type(e).__name__
    json.dump(rec, open(out, "w"), ensure_ascii=False, indent=1)
    print(img, "stop:", msg.stop_reason, "s:", rec["seconds"], "in:", u.input_tokens, "out:", u.output_tokens, "~$", rec["cost_usd_estimate"], "parsed:", "parsed" in rec)

main(sys.argv[1], sys.argv[2])
