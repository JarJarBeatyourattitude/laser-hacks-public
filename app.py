import json
from flask import Flask, request, jsonify, render_template
import random
from datetime import datetime, timedelta

app = Flask(__name__)

# --- LLM Simulation ---
# In a real app, this function would call an actual LLM API (like Google's Gemini API)
# For this hackathon demo, we simulate the reasoning and output.
def call_simulated_llm(student_data):
    """
    Simulates an LLM processing student data and proposing meetup petitions.

    Args:
        student_data (dict): Dictionary containing 'name', 'classes', 'availability'.

    Returns:
        list: A list of dictionaries, where each dictionary represents a potential meetup petition.
    """
    print(f"--- Simulating LLM for student: {student_data.get('name', 'Unknown')} ---")
    print(f"Raw Input Data: {student_data}")

    classes_str = student_data.get('classes', '')
    availability_str = student_data.get('availability', '')
    student_name = student_data.get('name', 'Student') # Get student name or use default

    # Basic parsing (a real LLM would do this much better)
    classes = [c.strip().upper() for c in classes_str.split(',') if c.strip()]
    
    # --- Simulated Reasoning ---
    reasoning_log = [
        f"Received request for {student_name}.",
        f"Identified classes: {classes if classes else 'None specified'}.",
        f"Raw availability input: '{availability_str}'. Analyzing potential slots.",
    ]

    proposed_petitions = []

    # --- Simple Rule-Based Petition Generation (Simulating LLM Output) ---
    # This is where a real LLM would analyze shared classes among multiple students,
    # understand complex availability descriptions, and suggest optimal times/locations.
    # We'll just create some generic suggestions based on the input classes.

    potential_times = [
        "Monday 3:00 PM", "Monday 5:00 PM",
        "Tuesday 1:00 PM", "Tuesday 4:00 PM",
        "Wednesday 11:00 AM", "Wednesday 2:00 PM",
        "Thursday 10:00 AM", "Thursday 3:30 PM",
        "Friday 12:00 PM", "Friday 2:00 PM"
    ]
    potential_locations = [
        "Library Study Room 2A", "Student Activities Center (SAC) Cafeteria",
        "Outside BSTIC building", "Learning Resource Center (LRC)", "Online (Zoom/Discord)"
    ]

    if not classes:
        reasoning_log.append("No specific classes provided. Suggesting a general social meetup.")
        proposed_petitions.append({
            "id": f"gen_social_{random.randint(1000,9999)}",
            "type": "Social Hangout",
            "subject": "General IVC Student Hangout",
            "details": f"Casual meetup proposed for {student_name} and others based on general availability patterns.",
            "proposed_time": random.choice(potential_times),
            "proposed_location": random.choice(potential_locations),
            "reasoning": " ".join(reasoning_log) + " Suggested social event due to lack of specific course input."
        })
    else:
        reasoning_log.append(f"Generating study group ideas for classes: {', '.join(classes)}.")
        # Create one potential petition per class mentioned (up to a limit)
        for course in classes[:3]: # Limit to avoid too many suggestions
            chosen_time = random.choice(potential_times)
            chosen_location = random.choice(potential_locations)
            petition_id = f"{course.replace(' ','_')}_{random.randint(1000,9999)}"
            
            current_reasoning = reasoning_log + [f"Focusing on {course}. Evaluating common free times (simulated). Found potential slot: {chosen_time} at {chosen_location}."]

            proposed_petitions.append({
                "id": petition_id,
                "type": "Study Group",
                "subject": f"{course} Study Session",
                "details": f"Proposed study group for {course}, initiated based on input from {student_name}.",
                "proposed_time": chosen_time,
                "proposed_location": chosen_location,
                "reasoning": " ".join(current_reasoning)
            })

    print(f"--- Simulation Complete. Proposed Petitions: {json.dumps(proposed_petitions, indent=2)} ---")
    return proposed_petitions

# --- API Endpoints ---

@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html')

@app.route('/generate_petitions', methods=['POST'])
def generate_petitions():
    """
    Receives student data, calls the simulated LLM,
    and returns proposed petitions as JSON.
    """
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    student_data = request.get_json()

    if not student_data or not isinstance(student_data, dict):
         return jsonify({"error": "Invalid JSON data provided"}), 400

    # Basic validation (can be expanded)
    if 'classes' not in student_data or 'availability' not in student_data:
        return jsonify({"error": "Missing 'classes' or 'availability' in request"}), 400

    # Call the simulated LLM function
    try:
        petitions = call_simulated_llm(student_data)
        return jsonify(petitions)
    except Exception as e:
        print(f"Error during LLM simulation: {e}") # Log the error server-side
        return jsonify({"error": "Failed to generate petitions", "details": str(e)}), 500


# --- Run the App ---
if __name__ == '__main__':
    # Note: debug=True is great for development but should be False in production
    app.run(debug=True, port=5001) # Using port 5001 to avoid conflicts