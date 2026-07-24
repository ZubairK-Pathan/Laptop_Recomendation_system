import json
import os

import ollama
import uvicorn
import pandas as pd

import functools
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from models.recommender import run_lapmatch

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")


class PromptRequest(BaseModel):
    prompt: str

class LaptopRequirements(BaseModel):
    budget: int = Field(
        description="Maximum budget in INR (₹). Extract numeric value only. "
        "If no budget is explicitly mentioned, default strictly to 80000."
    )
    q_perf: str = Field(
        description="Performance requirement. MUST output exactly 'A', 'B', or 'C'. "
        "'A' = Basic tasks (web browsing, MS Word). "
        "'B' = Moderate tasks (coding, light gaming, student work). "
        "'C' = Heavy tasks (3D rendering, hardcore AAA gaming, 4K video editing). "
        "NEGATIVE CONSTRAINT: Do NOT categorize words like 'portable', 'light', or 'battery' here."
    )
    q_port: str = Field(
        description="Portability and weight requirement. MUST output exactly 'A', 'B', or 'C'. "
        "'A' = Heavy, desk-bound laptop (user doesn't care about weight). "
        "'B' = Occasional travel. "
        "'C' = Ultra-light, Everyday Carry. "
        "KEYWORD TRIGGER: If the user mentions 'portable', 'lightweight', "
        "'travel', or 'carry', you MUST output 'C' here."
    )
    q_batt: str = Field(
        description="Battery life requirement. MUST output exactly 'A', 'B', or 'C'. "
        "'A' = Always plugged in / desk-bound. "
        "'B' = Moderate battery (4-5 hours). "
        "'C' = All-day battery. "
        "KEYWORD TRIGGER: If the user mentions 'lasts all day', 'good battery', "
        "or 'long battery', you MUST output 'C' here."
    )

class RecalculateRequest(BaseModel):
    budget: float
    q_perf: str
    q_port: str
    q_batt: str


system_extraction_prompt = """
You are a laptop requirements extraction AI.
Extract values from the user's text and output ONLY a raw JSON object. No markdown.

STRICT RULES — each field MUST be exactly one of the letters A, B, or C:

- budget: integer in INR. Default 80000 if not stated.
- q_perf:
    A = basic (web browsing, MS Office, casual use)
    B = moderate (coding, student work, light gaming)
    C = heavy (3D rendering, AAA gaming, 4K editing)
  Note: words like 'portable', 'light', 'battery', 'travel' do NOT affect q_perf.
- q_port:
    A = desk-bound, weight doesn't matter
    B = occasional travel
    C = daily carry / frequent travel / lightweight needed
  Trigger C if user says: travel, carry, portable, lightweight, on the go.
- q_batt:
    A = always plugged in
    B = 4-5 hours
    C = all day battery / long battery life
  Trigger C if user says: all day, last all day, good battery, long battery.

Output format (example):
{"budget": 80000, "q_perf": "B", "q_port": "C", "q_batt": "C"}
"""


def get_batch_engineer_reviews(user_prompt, laptops_data):
    lap_info = "\n".join(
        [
            f"Option {i + 1}: {lap['name']}, Price: ₹{lap['price']}, Match: {lap['score']}%"
            for i, lap in enumerate(laptops_data)
        ]
    )
    review_prompt = (
        f"User Request: '{user_prompt}'. Matches:\n{lap_info}\n"
        "Provide a brutally technical, 1-sentence rationale for EACH option. Return ONLY a JSON schema. "
        "DO NOT start the sentence with 'Option 1:', the laptop's name, or any prefixes. Go straight into the description and why it fits the user."
    )

    schema = {
        "type": "object",
        "properties": {
            "reviews": {"type": "array", "items": {"type": "string", "description": "1 sentence technical rationale"}}
        },
        "required": ["reviews"],
    }

    content = ""

    # ATTEMPT 1: Local Ollama
    try:
        response = ollama.chat(
            model="llama3:8b",
            messages=[
                {
                    "role": "system",
                    "content": "You are a Senior Hardware Engineer. "
                    "Reply strictly with the requested JSON schema array of rationales.",
                },
                {"role": "user", "content": review_prompt},
            ],
            stream=False,
            format=schema,
        )
        content = response["message"]["content"]

    except Exception as e:
        print(f"Local Ollama failed for reviews: {e}. Trying Gemini fallback...")
        
        # ATTEMPT 2: Gemini Cloud Fallback
        if not gemini_client:
            print("No GEMINI_API_KEY found. Falling back to default algorithm text.")
            return ["Mathematical proximity map optimized."] * len(laptops_data)
            
        try:
            full_prompt = (
                "You are a Senior Hardware Engineer. Reply strictly in JSON format "
                "with a single key 'reviews' containing an array of 1-sentence rationales.\n\n"
                f"{review_prompt}"
            )
            for attempt in range(3):
                try:
                    response = gemini_client.models.generate_content(
                        model='gemini-3.5-flash',
                        contents=full_prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                        ),
                    )
                    break
                except Exception as retry_e:
                    if "503" in str(retry_e) and attempt < 2:
                        import time
                        time.sleep(2)
                    else:
                        raise retry_e
            content = response.text
        except Exception as gemini_e:
            print(f"Gemini API failed: {gemini_e}")
            return ["Mathematical proximity map optimized."] * len(laptops_data)

    # Parse resulting JSON from either provider
    try:
        content = content.replace("```json", "").replace("```", "").strip()
        data = json.loads(content)
        return data.get("reviews", [])
    except Exception as parse_e:
        print(f"JSON Parsing error: {parse_e}")
        return ["Mathematical proximity map optimized."] * len(laptops_data)


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@functools.lru_cache(maxsize=128)
def extract_intent(prompt: str) -> dict:
    raw_json = None
    # 1. Extract Intent - ATTEMPT 1: Local Ollama
    try:
        response = ollama.chat(
            model="llama3:8b",
            messages=[
                {"role": "system", "content": system_extraction_prompt},
                {"role": "user", "content": prompt},
            ],
            format=LaptopRequirements.model_json_schema(),
        )
        raw_json = response["message"]["content"]
    except Exception as e:
        print(f"Local Ollama extraction failed: {e}. Trying Gemini fallback...")
        if not gemini_client:
            print("Local AI is offline and no GEMINI_API_KEY was found. Using fallback.")
            return {"budget": 80000, "q_perf": "B", "q_port": "B", "q_batt": "B"}
            
        try:
            full_prompt = f"{system_extraction_prompt}\n\nUser Request: {prompt}"
            for attempt in range(3):
                try:
                    response = gemini_client.models.generate_content(
                        model='gemini-3.5-flash',
                        contents=full_prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                        ),
                    )
                    break
                except Exception as retry_e:
                    if "503" in str(retry_e) and attempt < 2:
                        import time
                        time.sleep(2)
                    else:
                        raise retry_e
            raw_json = response.text
        except Exception as gemini_e:
            print(f"Gemini API failed: {gemini_e}")
            return {"budget": 80000, "q_perf": "B", "q_port": "B", "q_batt": "B"}

    try:
        cleaned_json = raw_json.replace("```json", "").replace("```", "").strip()
        extracted_data = json.loads(cleaned_json)
        return extracted_data
    except Exception as e:
        print(f"LLM JSON Parsing failed: {str(e)}\nRaw: {raw_json}")
        return {"budget": 80000, "q_perf": "B", "q_port": "B", "q_batt": "B"}


@app.post("/api/recommend")
async def recommend(request: PromptRequest):
    extracted_data = extract_intent(request.prompt)

    # 2. Normalize LLM output to valid A/B/C values
    def normalize_abc(value: str, default: str = "B") -> str:
        if not isinstance(value, str):
            return default
        v = value.strip().upper()
        if v in ("A", "B", "C"):
            return v
        perf_map = {"BASIC": "A", "GENERAL": "B", "MODERATE": "B", "HEAVY": "C", "HIGH": "C"}
        port_map = {
            "DESKBOUND": "A", "DESK-BOUND": "A", "STATIONARY": "A", "OCCASIONAL": "B", 
            "DURABLE": "B", "PORTABLE": "C", "ULTRALIGHT": "C", "EVERYDAY": "C", "DAILY": "C"
        }
        batt_map = {
            "PLUGGEDIN": "A", "PLUGGED-IN": "A", "SHORT": "A", "MODERATE": "B", "MEDIUM": "B", 
            "ALLDAY": "C", "ALL-DAY": "C", "LONG": "C", "LONGLASTING": "C", "LONG-LASTING": "C"
        }
        combined = {**perf_map, **port_map, **batt_map}
        return combined.get(v.replace(" ", ""), default)

    # 3. Run TOPSIS Math
    budget = int(extracted_data.get("budget", 80000))
    q_perf = normalize_abc(extracted_data.get("q_perf", "B"))
    q_port = normalize_abc(extracted_data.get("q_port", "B"))
    q_batt = normalize_abc(extracted_data.get("q_batt", "B"))

    extracted_data["q_perf"] = q_perf
    extracted_data["q_port"] = q_port
    extracted_data["q_batt"] = q_batt

    recommendations = run_lapmatch(budget, q_perf, q_port, q_batt, flex=0.3)

    if recommendations.empty:
        return {"error": "No laptops found under that budget."}

    # 4. Generate Engineer rationale in BATCH
    batch_input = []
    for i, row in recommendations.reset_index().iterrows():
        batch_input.append({"name": row["name"], "price": row["price"], "score": round(row["Match_Score"] * 100, 1)})

    rationales = get_batch_engineer_reviews(request.prompt, batch_input)

    results = []
    for i, row in recommendations.reset_index().iterrows():
        if i < len(rationales):
            raw_rat = rationales[i]
            if isinstance(raw_rat, dict):
                rationale = raw_rat.get("rationale", raw_rat.get("reason", raw_rat.get("review", "")))
                if not rationale and len(raw_rat) > 0:
                    rationale = str(list(raw_rat.values())[0])
            elif isinstance(raw_rat, str) and raw_rat.strip().startswith("{"):
                try:
                    inner_dict = json.loads(raw_rat)
                    rationale = inner_dict.get("rationale", inner_dict.get("reason", inner_dict.get("review", "")))
                    if not rationale and len(inner_dict) > 0:
                        rationale = str(list(inner_dict.values())[0])
                except Exception:
                    rationale = raw_rat
            else:
                rationale = str(raw_rat)
        else:
            rationale = "Algorithm determined optimal vector proximity."
            
        import re
        # Remove any prefixes like "Option 1: Infinix Zero Book -" or just the laptop name
        rationale = re.sub(r'^(Option \d+:.*?-\s*|.*?- \s*)', '', rationale).strip()
        # Fallback if it still starts with "Option X:"
        rationale = re.sub(r'^Option \d+:\s*', '', rationale).strip()

        results.append(
            {
                "name": row["name"],
                "price": row["price"],
                "match_score": row["Match_Score"],
                "weight": row["Weight_Proxy"],
                "battery": row["Battery_Proxy"],
                "pitch": rationale,
                "image_url": row["img"] if pd.notna(row.get("img")) else "https://placehold.co/400x250?text=No+Image",
                "specs": {
                    "processor": row.get("processor", "Unknown"),
                    "ram": row.get("ram", "Unknown"),
                    "storage": row.get("storage", "Unknown"),
                    "graphics_card": row.get("graphics_card", "Unknown")
                }
            }
        )

    return {"results": results, "calculations": {"extracted_intent": extracted_data}}


@app.get("/api/stats")
async def get_stats():
    from pathlib import Path
    file_path = Path(__file__).resolve().parent / "data" / "lapmatch_clean_data.csv"
    if not file_path.exists():
        return {"error": "Data file not found."}
    
    df = pd.read_csv(file_path)
    
    # Simple market insights logic (e.g. brand counts)
    df["brand"] = df["name"].apply(lambda x: str(x).split()[0] if pd.notna(x) else "Unknown")
    brand_counts = df["brand"].value_counts().head(10).to_dict()
    
    return {
        "market_insights": {
            "top_brands": brand_counts,
            "total_laptops": len(df),
            "avg_price": float(df["price"].mean())
        }
    }


@app.post("/api/recalculate")
async def recalculate(req: RecalculateRequest):
    recommendations = run_lapmatch(req.budget, req.q_perf, req.q_port, req.q_batt, flex=0.3)
    
    if recommendations.empty:
        return {"error": "No laptops found under that budget."}
        
    results = []
    for _, row in recommendations.reset_index().iterrows():
        results.append({
            "name": row["name"],
            "price": row["price"],
            "match_score": row["Match_Score"],
            "weight": row["Weight_Proxy"],
            "battery": row["Battery_Proxy"],
            "pitch": "Recalculated instantly based on custom mathematical weights.",
            "image_url": row["img"] if pd.notna(row.get("img")) else "https://placehold.co/400x250?text=No+Image",
            "specs": {
                "processor": row.get("processor", "Unknown"),
                "ram": row.get("ram", "Unknown"),
                "storage": row.get("storage", "Unknown"),
                "graphics_card": row.get("graphics_card", "Unknown")
            }
        })
        
    return {"results": results, "calculations": {"extracted_intent": req.dict()}}


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)