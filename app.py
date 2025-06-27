from flask_cors import CORS, cross_origin
from flask import Flask, request, jsonify
from ask_gpt import answer_question
import os
import requests  # For logging to Google Sheets

app = Flask(__name__)
CORS(app)

@app.route("/ask", methods=["POST"])
def ask():
    try:
        data = request.get_json()
        question = data.get("question", "")
        mode = data.get("mode", "default")
        tags = data.get("tags", [])
        output_format = data.get("output_format", "markdown")

        result = answer_question(
            question=question,
            mode=mode,
            tags=tags,
            output_format=output_format
        )

        if output_format == "json":
            return jsonify(result)
        else:
            return result, 200, {"Content-Type": "text/markdown"}

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/log", methods=["POST"])
@cross_origin()  # ✅ Enables CORS for Carrd's POST logging
def log_to_google_sheets():
    try:
        data = request.json
        payload = {
            "question": data.get("question", ""),
            "answer": data.get("answer", ""),
            "ip": data.get("ip", "N/A")
        }
        res = requests.post(
            "https://script.google.com/macros/s/AKfycbyxcVBy2boBhHuPyXS-I4b_DA_2zW8lBMZ_8e9499aWct9OFLUMAKKZRLS28SdjiKjt/exec",
            json=payload
        )
        return {"status": "logged", "code": res.status_code}
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"✅ Server starting on port {port}...")
    app.run(debug=False, host="0.0.0.0", port=port)

