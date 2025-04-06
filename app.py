import json
import os
import random
import re  # For filename sanitization
import requests  # For downloading images
import io  # For handling image data
from pathlib import Path  # For path manipulation
from datetime import datetime, timedelta, timezone

try:
    from dateutil import parser as dateutil_parser

    HAS_DATEUTIL = True
except ImportError:
    HAS_DATEUTIL = False
    print("WARN: dateutil not found")

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    redirect,
    url_for,
    flash,
    session,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc, asc, nullslast, Text  # Import Text type
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from email_validator import validate_email, EmailNotValidError
from openai import OpenAI

# --- App Configuration ---
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "FLASK_SECRET_KEY", "dev-secret-key-please-change"
)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
STATIC_FOLDER = Path(app.static_folder or "static")
IMAGES_FOLDER = STATIC_FOLDER / "images"
IMAGES_FOLDER.mkdir(parents=True, exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"

# --- Database Models ---


class Comment(db.Model):
    # (Unchanged)
    __tablename__ = "comment"
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    event_id = db.Column(
        db.Integer, db.ForeignKey("event.id"), nullable=False, index=True
    )

    def __repr__(self):
        return f"<Comment {self.id} by User:{self.user_id} on Event:{self.event_id}>"


class User(UserMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # --- NEW: Profile Fields ---
    availability_prefs = db.Column(
        Text, nullable=True
    )  # Store user's general availability text
    study_subjects = db.Column(
        Text, nullable=True
    )  # Store comma-separated or newline-separated subjects

    # Relationships (Unchanged)
    events = db.relationship(
        "Event", backref="creator", lazy="dynamic", cascade="all, delete-orphan"
    )
    commitments = db.relationship(
        "Commitment", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )
    comments = db.relationship(
        "Comment", backref="author", lazy="dynamic", cascade="all, delete-orphan"
    )

    # (Methods set_password, check_password unchanged)
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.email}>"


class Event(db.Model):
    # (Unchanged)
    __tablename__ = "event"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )  # Creator/Proposer
    subject = db.Column(db.String(200), nullable=False, index=True)
    type = db.Column(
        db.String(50), nullable=True, default="Study Session"
    )  # Default type
    proposed_time_string = db.Column(
        db.String(100), nullable=True
    )  # Store original string if parsing fails
    proposed_datetime = db.Column(db.DateTime, nullable=True, index=True)
    proposed_location = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text, nullable=True)
    reasoning = db.Column(
        db.Text, nullable=True
    )  # Could be AI reasoning or manual note
    status = db.Column(db.String(20), default="proposed", index=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    image_filename = db.Column(db.String(255), nullable=True)
    # Relationships
    commitments = db.relationship(
        "Commitment", backref="event", cascade="all, delete-orphan"
    )
    comments = db.relationship(
        "Comment",
        backref="event",
        cascade="all, delete-orphan",
        order_by=Comment.created_at.asc(),
    )

    def __repr__(self):
        return f"<Event {self.id}: {self.subject}>"


class Commitment(db.Model):
    # (Unchanged)
    __tablename__ = "commitment"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    event_id = db.Column(
        db.Integer, db.ForeignKey("event.id"), nullable=False, index=True
    )
    committed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        db.UniqueConstraint("user_id", "event_id", name="_user_event_uc"),
    )

    def __repr__(self):
        return f"<Commitment User:{self.user_id} to Event:{self.event_id}>"


# --- Flask-Login Setup ---
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# --- Helper Functions ---
def parse_datetime_string(datetime_str):
    # (Unchanged)
    if not datetime_str:
        return None
    try:
        dt = datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
        dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        return dt.astimezone(timezone.utc)
    except ValueError:
        if HAS_DATEUTIL:
            try:
                dt = dateutil_parser.parse(datetime_str, fuzzy=False)
                dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
                return dt.astimezone(timezone.utc)
            except Exception as e:
                print(f"dateutil parse failed: {e}")
                return None
        else:
            print(f"ISO parse failed: {datetime_str}")
            return None
    except Exception as e:
        print(f"Generic parse error: {e}")
        return None


def sanitize_filename(text, max_length=50):
    # (Unchanged)
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"\s+", "_", text).strip("_")
    return text[:max_length]


def generate_event_image(subject):
    # (Unchanged - includes the GPT-4o similarity check from previous step)
    if not subject:
        return None
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set.")
        return None
    client = OpenAI(api_key=api_key)
    try:
        existing_files = [f.stem for f in IMAGES_FOLDER.glob("*.png") if f.is_file()]
        if existing_files:
            print(
                f"Checking for images similar to '{subject}' among {len(existing_files)} existing files..."
            )
            prompt_subject = f"Find the best existing image filename stem that closely matches the semantic meaning of the event subject: '{subject}'."
            prompt_files = (
                "Existing filename stems related to previous event subjects are:\n"
                + "\n".join([f"- {fname}" for fname in existing_files])
            )
            prompt_instruction = "Analyze the subject and the list of existing stems. If you find a stem that is a very close semantic match (represents essentially the same topic or activity), return ONLY the matching stem string (e.g., 'calculus_study_group'). If multiple are close, return the best one. If none of the existing stems are a close semantic match, return ONLY the string 'None'."
            try:
                print("Calling GPT-4o to check for similar images...")
                similarity_response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an assistant comparing event subjects to existing image filename stems.",
                        },
                        {
                            "role": "user",
                            "content": f"{prompt_subject}\n\n{prompt_files}\n\n{prompt_instruction}",
                        },
                    ],
                    temperature=0.2,
                    max_tokens=100,
                )
                match_result = similarity_response.choices[0].message.content.strip()
                print(f"GPT-4o similarity check result: '{match_result}'")
                if (
                    match_result
                    and match_result.lower() != "none"
                    and match_result in existing_files
                ):
                    matched_filename = f"{match_result}.png"
                    relative_filepath = f"images/{matched_filename}"
                    print(
                        f"Found semantically similar existing image: {relative_filepath}. Reusing."
                    )
                    return relative_filepath
            except Exception as ai_err:
                print(
                    f"ERROR: GPT-4o similarity check failed: {ai_err}. Proceeding to generate new image."
                )
    except Exception as file_err:
        print(
            f"ERROR: Could not list existing image files: {file_err}. Proceeding to generate new image."
        )
    safe_subject_base = sanitize_filename(subject)
    if not safe_subject_base:
        return None
    exact_filename = f"{safe_subject_base}.png"
    exact_relative_filepath = f"images/{exact_filename}"
    exact_absolute_filepath = IMAGES_FOLDER / exact_filename
    if exact_absolute_filepath.exists():
        print(
            f"Image found for exact sanitized name '{safe_subject_base}': {exact_relative_filepath}"
        )
        return exact_relative_filepath
    print(
        f"No suitable existing image found. Generating new DALL-E 3 image for '{subject}'..."
    )
    try:
        dalle_prompt = f"Clean, modern graphic representing college students studying or related to: {subject}. Suitable for a web card. Minimalist vector style. 16:9 aspect ratio."
        response = client.images.generate(
            model="dall-e-3",
            prompt=dalle_prompt,
            size="1792x1024",
            quality="standard",
            n=1,
            response_format="url",
        )
        image_url = response.data[0].url
        print(f"DALL-E generated image URL: {image_url}")
        new_absolute_filepath = IMAGES_FOLDER / f"{safe_subject_base}.png"
        new_relative_filepath = f"images/{safe_subject_base}.png"
        image_response = requests.get(image_url, stream=True, timeout=30)
        image_response.raise_for_status()
        with open(new_absolute_filepath, "wb") as f:
            for chunk in image_response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"New image saved: {new_absolute_filepath}")
        return new_relative_filepath
    except requests.exceptions.RequestException as req_err:
        print(f"ERROR: Download failed {image_url}: {req_err}")
        return None
    except Exception as e:
        print(f"ERROR: DALL-E generation/save failed for '{subject}': {e}")
        new_absolute_filepath = IMAGES_FOLDER / f"{safe_subject_base}.png"
        if new_absolute_filepath.exists():
            try:
                new_absolute_filepath.unlink()
            except OSError:
                print(f"Warn: Could not remove partial file {new_absolute_filepath}")
        return None


# --- DEPRECATED: AI Interaction for Single Event Proposal ---
# This function is no longer used directly by a form, but might be adapted for the weekly scheduler
# def call_openai_llm(student_data, user_name="Student"): ...


# --- NEW: AI Interaction for Weekly Scheduling ---
def run_ai_weekly_scheduling():
    """Fetches user preferences and runs AI to propose a weekly schedule."""
    print("--- Starting Weekly AI Scheduling ---")
    try:
        # 1. Fetch relevant data for all active users (or a subset)
        users = User.query.filter(
            User.availability_prefs.isnot(None) | User.study_subjects.isnot(None)
        ).all()
        if not users:
            print("No users with preferences found. Skipping scheduling.")
            return 0, "No users with preferences found."

        user_data_list = []
        for user in users:
            user_data_list.append(
                {
                    "user_id": user.id,
                    "name": user.name or f"User {user.id}",
                    "availability": user.availability_prefs or "Not specified",
                    "subjects": user.study_subjects or "Any",
                }
            )

        if not user_data_list:
            print("No user data collected. Skipping scheduling.")
            return 0, "No user data to process."

        # 2. Prepare the prompt for the AI planner
        # This prompt needs to be complex, explaining the goal, the input format,
        # and the desired output format (list of event dictionaries).
        # It should consider the current date to schedule for the upcoming week.
        current_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        start_of_week = (datetime.now(timezone.utc)).date()
        end_of_week = start_of_week + timedelta(days=7)

        system_prompt = f"""You are a highly intelligent scheduling assistant for a college meetup platform (IVC Meetups, Irvine, CA).
Your task is to analyze the availability and study subject preferences of multiple students and propose a schedule of study sessions for the upcoming week ({start_of_week.isoformat()} to {end_of_week.isoformat()}).
Current UTC time is {current_time_str}. Assume standard Irvine working hours/study times unless availability specifies otherwise.

Input Data Format: A JSON list of user objects, each with 'user_id', 'name', 'availability' (text description), and 'subjects' (text description, possibly comma-separated).

Goal: Create group study sessions focusing on specific subjects where students share interests and have overlapping availability. Aim for sessions of 1-2 hours. Maximize participation but prioritize viable groups (at least 2 students). Suggest reasonable IVC campus locations (Library, SSC, Quad, online) or nearby cafes.

Output Format: Respond ONLY with a valid JSON object containing a single key "scheduled_events". The value MUST be a JSON list `[]`.
Each element in the list MUST be a JSON dictionary representing a single proposed event with EXACTLY these keys:
- "subject": (string) Specific subject for the session (e.g., "MATH 2A Review", "CIS 101 Project Work").
- "type": (string) e.g., "Study Group", "Project Collaboration".
- "proposed_datetime": (string) The proposed start time in strict ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ). MUST be within the target week.
- "proposed_location": (string) Specific location (e.g., "IVC Library Room 205", "Online via Discord").
- "details": (string) Brief description (e.g., "Focusing on Chapter 3 problems", "Collaborative work on phase 1").
- "reasoning": (string) Brief justification (e.g., "Matches availability for User 3, User 5 interested in Calculus").
- "involved_user_ids": (list of integers) List of user IDs identified as potential participants based on input.

Example Output Event:
{{
  "subject": "CHEM 1A Thermodynamics Practice",
  "type": "Study Group",
  "proposed_datetime": "{start_of_week.isoformat()}T14:00:00Z",
  "proposed_location": "IVC Library Group Study Room",
  "details": "Working through practice problems for Chapter 8.",
  "reasoning": "Users 2 and 7 indicated interest in CHEM 1A and afternoon availability.",
  "involved_user_ids": [2, 7]
}}

Analyze the provided student data carefully. Generate a reasonable number of sessions for the week. If no good matches are found, return an empty list: {{"scheduled_events": []}}.
Do NOT include explanations outside the JSON.
"""
        user_prompt = f"Student Preferences for Scheduling:\n{json.dumps(user_data_list, indent=2)}"

        # 3. Call the AI (Suggesting 3o-mini for complexity)
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key not found for scheduling")
        client = OpenAI(api_key=api_key)

        print(
            f"Sending scheduling request to AI (Model: o3-mini) for {len(user_data_list)} users..."
        )
        response = client.chat.completions.create(
            model="o3-mini",  # Using a more powerful model for this complex task
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        json_string = response.choices[0].message.content
        print("Received AI schedule proposal...")

        # 4. Parse the response and create events
        proposed_schedule = json.loads(json_string)
        created_count = 0
        system_user_id = 1  # Or fetch/create a dedicated system user ID

        if "scheduled_events" in proposed_schedule and isinstance(
            proposed_schedule["scheduled_events"], list
        ):
            event_proposals = proposed_schedule["scheduled_events"]
            print(f"AI proposed {len(event_proposals)} events.")
            saved_event_ids = []  # For image generation pass

            for event_data in event_proposals:
                # Validate required keys
                required_keys = [
                    "subject",
                    "type",
                    "proposed_datetime",
                    "proposed_location",
                    "details",
                    "reasoning",
                    "involved_user_ids",
                ]
                if not all(key in event_data for key in required_keys):
                    print(
                        f"Warning: Skipping invalid event proposal from AI (missing keys): {event_data}"
                    )
                    continue

                proposed_dt_str = event_data.get("proposed_datetime")
                parsed_datetime = parse_datetime_string(proposed_dt_str)
                if not parsed_datetime:
                    print(
                        f"Warning: Skipping event proposal with invalid datetime: {proposed_dt_str}"
                    )
                    continue

                # Simple check: Don't schedule in the past (relative to trigger time)
                if parsed_datetime < datetime.now(timezone.utc):
                    print(
                        f"Warning: Skipping event proposal scheduled in the past: {proposed_dt_str}"
                    )
                    continue

                try:
                    # Create the event, assign a system user or admin as creator
                    new_event = Event(
                        user_id=system_user_id,  # Assign to system/admin user
                        subject=event_data["subject"],
                        type=event_data["type"],
                        proposed_time_string=proposed_dt_str,
                        proposed_datetime=parsed_datetime,
                        proposed_location=event_data["proposed_location"],
                        details=event_data["details"],
                        reasoning=event_data["reasoning"]
                        + f" | AI Matched Users: {event_data['involved_user_ids']}",  # Add AI info
                        status="proposed",
                    )
                    db.session.add(new_event)
                    db.session.flush()  # Get the ID for image generation pass
                    saved_event_ids.append(
                        {"id": new_event.id, "subject": new_event.subject}
                    )
                    created_count += 1
                except Exception as e:
                    db.session.rollback()
                    print(
                        f"ERROR creating event object from AI proposal: {e} - Data: {event_data}"
                    )

            if created_count > 0:
                # Commit events first
                db.session.commit()
                print(
                    f"Successfully committed {created_count} events generated by AI scheduler."
                )

                # Second Pass: Generate Images for newly created events
                num_images_assigned = 0
                for event_info in saved_event_ids:
                    event_id = event_info["id"]
                    event_subject = event_info["subject"]
                    print(
                        f"Image assignment attempt for AI Event {event_id}, Subject: '{event_subject}'"
                    )
                    image_rel_path = generate_event_image(
                        event_subject
                    )  # Uses the function with similarity check
                    if image_rel_path:
                        try:
                            event_to_update = db.session.get(Event, event_id)
                            if event_to_update:
                                event_to_update.image_filename = image_rel_path
                                db.session.add(event_to_update)
                                num_images_assigned += 1
                            else:
                                print(
                                    f"Warn: AI Event {event_id} not found for image update."
                                )
                        except Exception as e:
                            print(
                                f"Error updating image filename for AI event {event_id}: {e}"
                            )

                if num_images_assigned > 0:
                    try:
                        db.session.commit()
                        print(
                            f"Committed images/assignments for {num_images_assigned} AI events."
                        )
                    except Exception as e:
                        db.session.rollback()
                        print(
                            f"ERROR: Commit image filenames failed for AI events: {e}"
                        )

            return (
                created_count,
                f"AI scheduling complete. Proposed {len(event_proposals)} events, created {created_count}.",
            )
        else:
            print("AI did not return the expected 'scheduled_events' list structure.")
            return 0, "AI did not return valid schedule structure."

    except Exception as e:
        db.session.rollback()
        print(f"CRITICAL ERROR during AI weekly scheduling: {e}")
        return 0, f"An error occurred during AI scheduling: {e}"


# --- Routes ---


@app.route("/")
def index():
    # (Unchanged)
    if not current_user.is_authenticated:
        flash("Please log in or sign up.", "info")
        return redirect(url_for("login"))
    subject_filter = request.args.get("subject", "").strip()
    sort_by = request.args.get("sort_by", "event_time_asc")
    committed_events = []
    events = []
    now_utc = datetime.now(timezone.utc)
    try:
        committed_events = (
            Event.query.join(Commitment)
            .filter(Commitment.user_id == current_user.id)
            .filter(Event.proposed_datetime.isnot(None))
            .filter(Event.proposed_datetime > now_utc)
            .order_by(Event.proposed_datetime.asc())
            .all()
        )
    except Exception as e:
        print(f"Error fetching committed events: {e}")
        flash("Error loading your commitments.", "warning")
    try:
        query = Event.query.options(
            db.joinedload(Event.creator),
            db.subqueryload(Event.comments).joinedload(Comment.author),
            db.subqueryload(Event.commitments).joinedload(Commitment.user),
        )
        if subject_filter:
            query = query.filter(Event.subject.ilike(f"%{subject_filter}%"))
        if sort_by == "date_asc":
            query = query.order_by(Event.created_at.asc())
        elif sort_by == "date_desc":
            query = query.order_by(Event.created_at.desc())
        elif sort_by == "event_time_asc":
            query = query.order_by(nullslast(Event.proposed_datetime.asc()))
        else:
            query = query.order_by(nullslast(Event.proposed_datetime.asc()))
        events = query.all()
    except Exception as e:
        print(f"Error fetching all events: {e}")
        flash("Error loading events.", "error")
    current_filters = {"subject": subject_filter, "sort_by": sort_by}
    return render_template(
        "index.html",
        events=events,
        committed_events=committed_events,
        filters=current_filters,
    )


@app.route("/calendar")
@login_required
def calendar_view():
    # (Unchanged)
    return render_template("calendar.html")


@app.route("/api/events_for_calendar")
@login_required
def api_events_for_calendar():
    # (Unchanged)
    try:
        events = Event.query.filter(Event.proposed_datetime.isnot(None)).all()
        calendar_events = []
        for event in events:
            start_time = event.proposed_datetime.isoformat()
            event_url = url_for("index", _anchor=f"event-{event.id}", _external=False)
            calendar_events.append(
                {
                    "id": event.id,
                    "title": event.subject,
                    "start": start_time,
                    "url": event_url,
                    "description": event.details or "",
                    "location": event.proposed_location or "",
                }
            )
        return jsonify(calendar_events)
    except Exception as e:
        print(f"Error calendar API: {e}")
        return jsonify({"error": "Fetch error"}), 500


# --- DEPRECATED: Single AI Proposal Form ---
# This route is replaced by manual creation and profile-based AI scheduling
# @app.route('/propose', ...)


# --- NEW: Manual Event Creation ---
@app.route("/create_event", methods=["GET", "POST"])
@login_required
def create_event():
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        event_type = request.form.get("type", "Study Session").strip()
        location = request.form.get("location", "").strip()
        datetime_str = request.form.get("datetime", "").strip()
        details = request.form.get("details", "").strip()

        errors = False
        if not subject:
            flash("Event subject is required.", "error")
            errors = True
        if not location:
            flash("Event location is required.", "error")
            errors = True
        if not datetime_str:
            flash("Event date and time are required.", "error")
            errors = True

        parsed_datetime = parse_datetime_string(
            datetime_str.replace(" ", "T") + ":00Z"
        )  # Attempt basic ISO conversion
        if not parsed_datetime:
            # Try parsing with dateutil if available and basic fails
            if HAS_DATEUTIL:
                try:
                    parsed_datetime = dateutil_parser.parse(datetime_str)
                    if (
                        parsed_datetime.tzinfo is None
                    ):  # Assume UTC if no timezone provided
                        parsed_datetime = parsed_datetime.replace(tzinfo=timezone.utc)
                    else:
                        parsed_datetime = parsed_datetime.astimezone(
                            timezone.utc
                        )  # Convert to UTC
                except Exception:
                    flash(
                        "Invalid date/time format. Please use YYYY-MM-DD HH:MM or a recognizable format.",
                        "error",
                    )
                    errors = True
            else:
                flash(
                    "Invalid date/time format. Please use YYYY-MM-DD HH:MM format.",
                    "error",
                )
                errors = True

        if errors:
            return render_template("create_event.html", form_data=request.form)

        try:
            new_event = Event(
                user_id=current_user.id,  # Created by current user
                subject=subject,
                type=event_type,
                proposed_datetime=parsed_datetime,
                proposed_location=location,
                details=details,
                proposed_time_string=datetime_str,  # Store original input
                status="proposed",  # Or maybe 'confirmed' if manual?
            )
            db.session.add(new_event)
            db.session.flush()  # Needed to get ID for image generation

            # Generate image for the new manual event
            image_rel_path = generate_event_image(new_event.subject)
            if image_rel_path:
                new_event.image_filename = image_rel_path
                db.session.add(new_event)  # Add again to update image path

            db.session.commit()
            flash(f"Event '{new_event.subject}' created successfully!", "success")
            return redirect(url_for("index"))

        except Exception as e:
            db.session.rollback()
            print(f"Error creating manual event: {e}")
            flash("An error occurred while creating the event.", "error")
            return render_template("create_event.html", form_data=request.form)

    # GET request
    return render_template("create_event.html")


# --- NEW: User Profile Page ---
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = db.session.get(User, current_user.id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        user.name = request.form.get("name", "").strip()
        user.phone = request.form.get("phone", "").strip()
        user.availability_prefs = request.form.get("availability_prefs", "").strip()
        user.study_subjects = request.form.get("study_subjects", "").strip()

        # Optional: Password change logic (add password fields to form if needed)
        # password = request.form.get('new_password')
        # confirm = request.form.get('confirm_password')
        # if password:
        #     if password == confirm:
        #         user.set_password(password)
        #         flash('Password updated successfully.', 'success')
        #     else:
        #         flash('New passwords do not match.', 'error')

        try:
            db.session.add(user)
            db.session.commit()
            flash("Profile updated successfully!", "success")
        except Exception as e:
            db.session.rollback()
            print(f"Error updating profile: {e}")
            flash("Error updating profile.", "error")

        return redirect(url_for("profile"))  # Redirect back to profile page

    # GET request
    return render_template("profile.html", user=user)


# --- NEW: Admin Page and Trigger ---
@app.route("/admin")
@login_required
def admin_page():
    # Basic role check (replace with proper role management if needed)
    if current_user.id != 1:  # Example: Only user ID 1 is admin
        flash("Access denied. Admin privileges required.", "error")
        return redirect(url_for("index"))
    return render_template("admin.html")


@app.route("/admin/trigger_schedule", methods=["POST"])
@login_required
def trigger_schedule():
    # Basic role check
    if current_user.id != 1:
        return jsonify({"error": "Access denied"}), 403

    try:
        count, message = run_ai_weekly_scheduling()
        if count > 0:
            flash(f"AI Scheduling triggered: {message}", "success")
        else:
            flash(f"AI Scheduling run: {message}", "info")
    except Exception as e:
        flash(f"Error running AI scheduler: {e}", "error")

    return redirect(url_for("admin_page"))


# --- Authentication Routes (Unchanged) ---
@app.route("/commit/<int:event_id>", methods=["POST"])
@login_required
def commit_to_event(event_id):
    # (Unchanged)
    event = db.session.get(Event, event_id)
    if not event:
        return jsonify({"error": "Event not found"}), 404
    if Commitment.query.filter_by(user_id=current_user.id, event_id=event_id).first():
        return jsonify({"error": "Already committed"}), 409
    try:
        db.session.add(Commitment(user_id=current_user.id, event_id=event_id))
        db.session.commit()
        user_contact = current_user.phone or current_user.email
        print(
            f"--- REMINDER SIM: User {current_user.email} committed to '{event.subject}'. Contact: {user_contact} ---"
        )
        return (
            jsonify(
                {
                    "message": "Committed",
                    "user_name": current_user.name or current_user.email,
                }
            ),
            201,
        )
    except Exception as e:
        db.session.rollback()
        print(f"Error commit: {e}")
        return jsonify({"error": "DB error"}), 500


@app.route("/event/<int:event_id>/comment", methods=["POST"])
@login_required
def add_comment(event_id):
    # (Unchanged)
    event = db.session.get(Event, event_id)
    if not event:
        flash("Event not found.", "error")
        return redirect(url_for("index"))
    comment_text = request.form.get("comment_text", "").strip()
    if not comment_text:
        flash("Comment cannot be empty.", "error")
        return redirect(url_for("index", _anchor=f"event-{event_id}"))
    try:
        new_comment = Comment(
            text=comment_text, user_id=current_user.id, event_id=event_id
        )
        db.session.add(new_comment)
        db.session.commit()
        flash("Comment added!", "success")
    except Exception as e:
        db.session.rollback()
        print(f"Error adding comment: {e}")
        flash("Error adding comment.", "error")
    return redirect(url_for("index", _anchor=f"event-{event_id}"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    # (Unchanged)
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        error = False
        if not email or not password or not confirm_password:
            flash("Required fields missing.", "error")
            error = True
        if password != confirm_password:
            flash("Passwords do not match.", "error")
            error = True
        try:
            valid_email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError as e:
            flash(f"Invalid email: {e}", "error")
            error = True
            valid_email = None
        if valid_email and User.query.filter_by(email=valid_email).first():
            flash("Email already registered.", "warning")
            error = True
        if error:
            return render_template("signup.html", form_data=request.form)
        new_user = User(email=valid_email, name=name, phone=phone)
        new_user.set_password(password)
        db.session.add(new_user)
        try:
            db.session.commit()
            flash("Account created! Logged in.", "success")
            login_user(new_user)
            return redirect(url_for("index"))
        except Exception as e:
            db.session.rollback()
            flash("DB error creating account.", "error")
            print(f"Signup Error: {e}")
            return render_template("signup.html", form_data=request.form)
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    # (Unchanged)
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password")
        remember = True if request.form.get("remember") else False
        try:
            valid_email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError:
            flash("Invalid email format.", "error")
            return render_template("login.html", form_data=request.form)
        user = User.query.filter_by(email=valid_email).first()
        if not user or not user.check_password(password):
            flash("Invalid email or password.", "error")
            return render_template("login.html", form_data=request.form)
        login_user(user, remember=remember)
        flash("Logged in.", "success")
        next_page = request.args.get("next")
        if next_page and not next_page.startswith("/"):
            next_page = url_for("index")
        return redirect(next_page or url_for("index"))
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    # (Unchanged)
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


# --- Main Execution ---
if __name__ == "__main__":
    with app.app_context():
        print("Initializing DB...")
        db.create_all()  # This will add the new columns to User table if they don't exist
        print("DB Initialized/Updated.")
        # Optional: Create a default admin user if one doesn't exist (for testing trigger)
        if not User.query.get(1):
            print("Creating default admin user (ID 1)...")
            admin_user = User(id=1, email="admin@example.com", name="Admin")
            admin_user.set_password("admin")  # CHANGE THIS PASSWORD!
            db.session.add(admin_user)
            try:
                db.session.commit()
                print("Admin user created.")
            except Exception as e:
                db.session.rollback()
                print(f"Failed to create admin user: {e}")

    if not os.environ.get("OPENAI_API_KEY"):
        print("CRITICAL ERROR: OPENAI_API_KEY environment variable not set.")
    else:
        print("OpenAI API Key found.")

    app.run(debug=True, port=5001)
