import json
import os
import random
import re # For filename sanitization
import requests # For downloading images
import io # For handling image data
from pathlib import Path # For path manipulation

from datetime import datetime, timedelta, timezone
try: from dateutil import parser as dateutil_parser; HAS_DATEUTIL = True
except ImportError: HAS_DATEUTIL = False; print("WARN: dateutil not found")

from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc, asc, nullslast
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from email_validator import validate_email, EmailNotValidError
from openai import OpenAI # Make sure OpenAI is imported
# from dotenv import load_dotenv

# load_dotenv()

# --- App Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-please-change')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
STATIC_FOLDER = Path(app.static_folder or 'static')
IMAGES_FOLDER = STATIC_FOLDER / 'images'
IMAGES_FOLDER.mkdir(parents=True, exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# --- Database Models ---
# (Models User, Event, Commitment, Comment remain unchanged from previous version)
class Comment(db.Model):
    __tablename__ = 'comment'
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False, index=True)

    def __repr__(self):
        return f'<Comment {self.id} by User:{self.user_id} on Event:{self.event_id}>'

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    # Relationships
    events = db.relationship('Event', backref='creator', lazy='dynamic', cascade="all, delete-orphan")
    commitments = db.relationship('Commitment', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='author', lazy='dynamic', cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.email}>'

class Event(db.Model):
    __tablename__ = 'event'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    subject = db.Column(db.String(200), nullable=False, index=True)
    type = db.Column(db.String(50), nullable=True)
    proposed_time_string = db.Column(db.String(100), nullable=True)
    proposed_datetime = db.Column(db.DateTime, nullable=True, index=True)
    proposed_location = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text, nullable=True)
    reasoning = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='proposed', index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    image_filename = db.Column(db.String(255), nullable=True) # Store 'images/safe_subject.png'
    # Relationships
    commitments = db.relationship('Commitment', backref='event', cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='event', cascade="all, delete-orphan", order_by=Comment.created_at.asc())

    def __repr__(self):
        return f'<Event {self.id}: {self.subject}>'

class Commitment(db.Model):
    __tablename__ = 'commitment'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False, index=True)
    committed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint('user_id', 'event_id', name='_user_event_uc'),)

    def __repr__(self):
        return f'<Commitment User:{self.user_id} to Event:{self.event_id}>'


# --- Flask-Login Setup ---
@login_manager.user_loader
def load_user(user_id): return db.session.get(User, int(user_id))

# --- Helper Functions ---
def parse_datetime_string(datetime_str):
    # (Same as before)
    if not datetime_str: return None
    try: dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00')); dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt; return dt.astimezone(timezone.utc)
    except ValueError:
        if HAS_DATEUTIL:
            try: dt = dateutil_parser.parse(datetime_str, fuzzy=False); dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt; return dt.astimezone(timezone.utc)
            except Exception as e: print(f"dateutil parse failed: {e}"); return None
        else: print(f"ISO parse failed: {datetime_str}"); return None
    except Exception as e: print(f"Generic parse error: {e}"); return None

def sanitize_filename(text, max_length=50):
    # (Same as before)
    text = re.sub(r'[^\w\s-]', '', text.lower()); text = re.sub(r'\s+', '_', text).strip('_'); return text[:max_length]

# --- DALL-E Image Generation Helper ---
def generate_event_image(subject):
    """
    Generates or retrieves an image for the event subject.
    Checks for existing images semantically close to the subject using GPT-4o
    before generating a new one with DALL-E 3.
    """
    if not subject: return None

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: print("ERROR: OPENAI_API_KEY not set."); return None
    client = OpenAI(api_key=api_key) # Initialize client once

    # --- START: New logic to check for existing similar images ---
    try:
        existing_files = [f.stem for f in IMAGES_FOLDER.glob('*.png') if f.is_file()] # Get base names (stems)
        if existing_files:
            print(f"Checking for images similar to '{subject}' among {len(existing_files)} existing files...")

            # Prepare prompt for GPT-4o
            prompt_subject = f"Find the best existing image filename stem that closely matches the semantic meaning of the event subject: '{subject}'."
            prompt_files = "Existing filename stems related to previous event subjects are:\n" + "\n".join([f"- {fname}" for fname in existing_files])
            prompt_instruction = (
                "Analyze the subject and the list of existing stems. "
                "If you find a stem that is a very close semantic match (represents essentially the same topic or activity), "
                "return ONLY the matching stem string (e.g., 'calculus_study_group'). "
                "If multiple are close, return the best one. "
                "If none of the existing stems are a close semantic match, return ONLY the string 'None'."
            )

            # Call GPT-4o (using the model specified by the user for 'easy tasks')
            try:
                print("Calling GPT-4o to check for similar images...")
                similarity_response = client.chat.completions.create(
                    model="gpt-4o", # Use gpt-4o as requested for similarity check
                    messages=[
                        {"role": "system", "content": "You are an assistant comparing event subjects to existing image filename stems."},
                        {"role": "user", "content": f"{prompt_subject}\n\n{prompt_files}\n\n{prompt_instruction}"}
                    ],
                    temperature=0.2, # Lower temperature for more deterministic matching
                    max_tokens=100
                )
                match_result = similarity_response.choices[0].message.content.strip()
                print(f"GPT-4o similarity check result: '{match_result}'")

                if match_result and match_result.lower() != 'none' and match_result in existing_files:
                    # Found a close enough match
                    matched_filename = f"{match_result}.png"
                    relative_filepath = f"images/{matched_filename}"
                    print(f"Found semantically similar existing image: {relative_filepath}. Reusing.")
                    return relative_filepath # Return the path to the existing similar image

            except Exception as ai_err:
                print(f"ERROR: GPT-4o similarity check failed: {ai_err}. Proceeding to generate new image.")
                # Fall through to generate new image if similarity check fails

    except Exception as file_err:
        print(f"ERROR: Could not list existing image files: {file_err}. Proceeding to generate new image.")
        # Fall through to generate new image if listing files fails
    # --- END: New logic ---


    # --- Fallback/Original logic: Generate new image if no suitable existing one is found ---
    # First, still check for EXACT match using sanitized filename (cheap check)
    safe_subject_base = sanitize_filename(subject)
    if not safe_subject_base: return None # Cannot generate image without a base name
    exact_filename = f"{safe_subject_base}.png"
    exact_relative_filepath = f"images/{exact_filename}"
    exact_absolute_filepath = IMAGES_FOLDER / exact_filename

    if exact_absolute_filepath.exists():
        print(f"Image found for exact sanitized name '{safe_subject_base}': {exact_relative_filepath}");
        return exact_relative_filepath

    # If no exact match and no similar match found by AI (or AI check failed), generate a new one
    print(f"No suitable existing image found. Generating new DALL-E 3 image for '{subject}'...")
    try:
        dalle_prompt = f"Clean, modern graphic representing college students studying or related to: {subject}. Suitable for a web card. Minimalist vector style. 16:9 aspect ratio."
        response = client.images.generate(
            model="dall-e-3", # Use DALL-E 3 for actual image generation
            prompt=dalle_prompt,
            size="1792x1024",
            quality="standard",
            n=1,
            response_format="url"
        )
        image_url = response.data[0].url;
        print(f"DALL-E generated image URL: {image_url}")

        # Use the sanitized filename for saving the new image
        new_absolute_filepath = IMAGES_FOLDER / f"{safe_subject_base}.png"
        new_relative_filepath = f"images/{safe_subject_base}.png"

        image_response = requests.get(image_url, stream=True, timeout=30);
        image_response.raise_for_status()
        with open(new_absolute_filepath, 'wb') as f:
            for chunk in image_response.iter_content(chunk_size=8192): f.write(chunk)
        print(f"New image saved: {new_absolute_filepath}");
        return new_relative_filepath

    except requests.exceptions.RequestException as req_err: print(f"ERROR: Download failed {image_url}: {req_err}"); return None
    except Exception as e:
        print(f"ERROR: DALL-E generation/save failed for '{subject}': {e}")
        # Clean up potential partial file if generation failed
        new_absolute_filepath = IMAGES_FOLDER / f"{safe_subject_base}.png"
        if new_absolute_filepath.exists():
             try: new_absolute_filepath.unlink()
             except OSError: print(f"Warn: Could not remove partial file {new_absolute_filepath}")
        return None


# --- OpenAI LLM Interaction for Event Proposal ---
def call_openai_llm(student_data, user_name="Student"):
    # (Function remains unchanged from previous version - uses gpt-4o-mini)
    print(f"--- Calling OpenAI API for user: {user_name} ---")
    api_key = os.environ.get("OPENAI_API_KEY");
    if not api_key: raise ValueError("OpenAI API key not found")
    try: client = OpenAI(api_key=api_key)
    except Exception as e: raise ValueError(f"Failed to init OpenAI client: {e}")

    now_utc = datetime.now(timezone.utc)
    current_time_str = now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

    # Using the detailed system prompt from before
    system_prompt = f"""You are an assistant that helps college students schedule study sessions or meetups.
Based on the user's input about their classes, availability, and purpose, propose 1 to 3 specific event options.
Current UTC time is {current_time_str}. Assume the user is located near Irvine Valley College (IVC) in Irvine, California. Suggest reasonable locations on or near the IVC campus (e.g., Library, Student Services Center, a specific cafe nearby, online).

VERY IMPORTANT: Respond ONLY with a valid JSON object containing a single key "proposals". The value of "proposals" MUST be a JSON list `[]`.
Each element in the list MUST be a JSON dictionary `{{}}` representing a single event proposal.
Each event proposal dictionary MUST contain EXACTLY the following keys:
- "type": (string) A short category like "Study Session", "Project Work", "Meeting", "Social".
- "subject": (string) A concise title for the event (e.g., "Math 2A Study Group", "CIS Project Collaboration").
- "details": (string) A brief description of the event's purpose or agenda.
- "proposed_datetime": (string) The proposed start time in strict ISO 8601 format with UTC timezone (YYYY-MM-DDTHH:MM:SSZ). Do NOT use relative times like "tomorrow afternoon". Be specific.
- "proposed_location": (string) The specific suggested location (e.g., "IVC Library Room 210", "Online via Discord", "Starbucks near IVC").
- "reasoning": (string) Brief justification for why this time/location fits the user's request.

Example of the exact output format:
{{
  "proposals": [
    {{
      "type": "Study Session",
      "subject": "Calculus Midterm Review",
      "details": "Reviewing chapters 3-5 for the upcoming Math 2A midterm.",
      "proposed_datetime": "{current_time_str}", # Replace with actual proposed time
      "proposed_location": "IVC Library - Quiet Study Area",
      "reasoning": "Fits the user's afternoon availability and the need for a quiet study space."
    }}
  ]
}}

Do NOT include any other text, explanations, or keys in your response. Just the JSON object starting with `{{` and ending with `}}`.
"""

    user_prompt = f"Student info:\n{json.dumps(student_data)}"
    try:
        print(f"Sending request to OpenAI API (Current Time: {current_time_str})...")
        response = client.chat.completions.create(
            model="gpt-4o-mini", # Use gpt-4o-mini for event proposal (difficult task)
            messages=[{"role":"system","content":system_prompt},{"role":"user","content":user_prompt}],
            response_format={"type":"json_object"}
        )
        print("Received response...")
        json_string = response.choices[0].message.content; print(f"Raw Response:\n{json_string[:300]}...")

        # Using the updated parsing logic from before
        petitions_list = []
        try:
            parsed_response = json.loads(json_string)

            if isinstance(parsed_response, dict) and "proposals" in parsed_response and isinstance(parsed_response["proposals"], list):
                petitions_list = parsed_response["proposals"]
                print("Parsed 'proposals' list from dictionary.")
            else:
                if isinstance(parsed_response, list):
                     print("Warning: API returned a list directly instead of {'proposals': [...]}. Processing list.")
                     petitions_list = parsed_response
                else:
                    print(f"Warning: Unexpected JSON structure received. Expected dictionary with 'proposals' key containing a list, got: {type(parsed_response)}")
        except json.JSONDecodeError as json_err:
             print(f"ERROR: Failed JSON decode: {json_err}"); print(f"Received text: {json_string[:500]}...");
             raise ValueError("Invalid JSON from AI.")

        # Validation part remains the same
        valid_petitions=[];
        required_keys=["type","subject","details","proposed_datetime","proposed_location","reasoning"]
        for item in petitions_list:
            if isinstance(item,dict) and all(key in item for key in required_keys): valid_petitions.append(item)
            else: print(f"Warning: Invalid proposal format or missing keys in item: {item}")
        print(f"--- OpenAI OK. Valid Proposals: {len(valid_petitions)} ---");
        return valid_petitions

    except Exception as e:
        print(f"ERROR: OpenAI call failed: {e}");
        raise


# --- Routes ---
# (Routes index, calendar_view, api_events_for_calendar remain unchanged)
@app.route('/')
def index():
    # ... same as previous ...
    if not current_user.is_authenticated: flash('Please log in or sign up.', 'info'); return redirect(url_for('login'))
    subject_filter = request.args.get('subject', '').strip(); sort_by = request.args.get('sort_by', 'event_time_asc')
    committed_events = []; events = []
    now_utc = datetime.now(timezone.utc)
    try: committed_events = Event.query.join(Commitment).filter(Commitment.user_id==current_user.id).filter(Event.proposed_datetime.isnot(None)).filter(Event.proposed_datetime > now_utc).order_by(Event.proposed_datetime.asc()).all()
    except Exception as e: print(f"Error fetching committed events: {e}"); flash("Error loading your commitments.", "warning")
    try:
        query = Event.query.options(db.joinedload(Event.creator), db.subqueryload(Event.comments).joinedload(Comment.author), db.subqueryload(Event.commitments).joinedload(Commitment.user))
        if subject_filter: query = query.filter(Event.subject.ilike(f'%{subject_filter}%'))
        if sort_by == 'date_asc': query = query.order_by(Event.created_at.asc())
        elif sort_by == 'date_desc': query = query.order_by(Event.created_at.desc())
        elif sort_by == 'event_time_asc': query = query.order_by(nullslast(Event.proposed_datetime.asc()))
        else: query = query.order_by(nullslast(Event.proposed_datetime.asc()))
        events = query.all()
    except Exception as e: print(f"Error fetching all events: {e}"); flash("Error loading events.", "error")
    current_filters = { 'subject': subject_filter, 'sort_by': sort_by }
    return render_template('index.html', events=events, committed_events=committed_events, filters=current_filters)


@app.route('/calendar')
@login_required
def calendar_view(): return render_template('calendar.html')

@app.route('/api/events_for_calendar')
@login_required
def api_events_for_calendar():
    # ... same as previous ...
    try:
        events = Event.query.filter(Event.proposed_datetime.isnot(None)).all(); calendar_events = []
        for event in events:
            start_time = event.proposed_datetime.isoformat()
            event_url = url_for('index', _anchor=f'event-{event.id}', _external=False)
            calendar_events.append({'id': event.id, 'title': event.subject, 'start': start_time, 'url': event_url, 'description': event.details or '', 'location': event.proposed_location or ''})
        return jsonify(calendar_events)
    except Exception as e: print(f"Error calendar API: {e}"); return jsonify({"error": "Fetch error"}), 500

@app.route('/propose', methods=['GET', 'POST'])
@login_required
def propose_event_form():
    if request.method == 'POST':
        classes=request.form.get('classes','').strip();availability=request.form.get('availability','').strip();purpose=request.form.get('purpose','').strip()
        if not availability or not purpose: flash('Purpose and availability required.','error'); return render_template('propose_form.html',form_data=request.form)
        student_input_data={"classes":classes,"availability":availability,"purpose":purpose}
        try:
            user_identifier=current_user.name or current_user.email
            proposed_events_data=call_openai_llm(student_input_data,user_identifier) # Uses gpt-4o-mini
            if not proposed_events_data: flash('AI suggestions failed/invalid.','info'); return render_template('propose_form.html',form_data=request.form)

            saved_event_ids = []; num_created = 0
            # First Pass: Create Events
            for event_data in proposed_events_data:
                proposed_dt_str=event_data.get('proposed_datetime'); parsed_datetime=parse_datetime_string(proposed_dt_str)
                if not parsed_datetime: flash(f"Warn: Bad time '{proposed_dt_str}' for '{event_data.get('subject')}'.",'warning')
                try: new_event=Event(user_id=current_user.id,subject=event_data.get('subject'),type=event_data.get('type'),proposed_time_string=proposed_dt_str,proposed_datetime=parsed_datetime,proposed_location=event_data.get('proposed_location'),details=event_data.get('details'),reasoning=event_data.get('reasoning'),status='proposed'); db.session.add(new_event); db.session.flush(); saved_event_ids.append({'id': new_event.id, 'subject': new_event.subject}); num_created+=1
                except Exception as e: print(f"Error creating event obj: {e}"); flash("Warning: Couldn't save suggestion.","warning"); db.session.rollback()
            if num_created == 0: flash('Could not create event records.','error'); return render_template('propose_form.html',form_data=request.form)
            try: db.session.commit(); print(f"Committed {num_created} initial events.")
            except Exception as e: db.session.rollback(); print(f"ERROR: Failed initial commit: {e}"); flash('DB error saving events.','error'); return render_template('propose_form.html',form_data=request.form)

            # Second Pass: Generate/Assign Images
            num_images_assigned = 0
            for event_info in saved_event_ids:
                 event_id=event_info['id']; event_subject=event_info['subject']
                 print(f"Image assignment attempt for Event {event_id}, Subject: '{event_subject}'")
                 # Calls the UPDATED generate_event_image function which now includes the GPT-4o check
                 image_rel_path = generate_event_image(event_subject)
                 if image_rel_path:
                     try:
                         event_to_update=db.session.get(Event,event_id)
                         if event_to_update: event_to_update.image_filename = image_rel_path; db.session.add(event_to_update); num_images_assigned+=1
                         else: print(f"Warn: Event {event_id} not found for image update.")
                     except Exception as e: print(f"Error updating image filename for event {event_id}: {e}")

            if num_images_assigned > 0:
                try: db.session.commit(); print(f"Committed images/assignments for {num_images_assigned} events.")
                except Exception as e: db.session.rollback(); print(f"ERROR: Commit image filenames failed: {e}"); flash('Error saving image refs.','warning')

            flash(f'{num_created} proposal(s) created! {num_images_assigned} image(s) assigned/generated.','success')
            return redirect(url_for('index'))
        except ValueError as ve: flash(f'Config Error: {ve}','error')
        except Exception as e: db.session.rollback(); flash('Unexpected error during proposal.','error'); print(f"Error in /propose: {e}")
        return render_template('propose_form.html',form_data=request.form)
    return render_template('propose_form.html')

# (Routes commit_to_event, add_comment, signup, login, logout remain unchanged)
@app.route('/commit/<int:event_id>', methods=['POST'])
@login_required
def commit_to_event(event_id):
    # ... same as previous ...
    event = db.session.get(Event, event_id);
    if not event: return jsonify({"error": "Event not found"}), 404
    if Commitment.query.filter_by(user_id=current_user.id, event_id=event_id).first(): return jsonify({"error": "Already committed"}), 409
    try: db.session.add(Commitment(user_id=current_user.id, event_id=event_id)); db.session.commit(); user_contact = current_user.phone or current_user.email; print(f"--- REMINDER SIM: User {current_user.email} committed to '{event.subject}'. Contact: {user_contact} ---"); return jsonify({"message": "Committed", "user_name": current_user.name or current_user.email}), 201
    except Exception as e: db.session.rollback(); print(f"Error commit: {e}"); return jsonify({"error": "DB error"}), 500

@app.route('/event/<int:event_id>/comment', methods=['POST'])
@login_required
def add_comment(event_id):
    # ... same as previous ...
    event = db.session.get(Event, event_id);
    if not event: flash("Event not found.", "error"); return redirect(url_for('index'))
    comment_text = request.form.get('comment_text', '').strip()
    if not comment_text: flash("Comment cannot be empty.", "error"); return redirect(url_for('index', _anchor=f'event-{event_id}'))
    try: new_comment = Comment(text=comment_text, user_id=current_user.id, event_id=event_id); db.session.add(new_comment); db.session.commit(); flash("Comment added!", "success")
    except Exception as e: db.session.rollback(); print(f"Error adding comment: {e}"); flash("Error adding comment.", "error")
    return redirect(url_for('index', _anchor=f'event-{event_id}'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    # ... same as previous ...
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        email=request.form.get('email','').strip();password=request.form.get('password');confirm_password=request.form.get('confirm_password');name=request.form.get('name','').strip();phone=request.form.get('phone','').strip();error=False
        if not email or not password or not confirm_password:flash('Required fields missing.','error');error=True
        if password!=confirm_password:flash('Passwords do not match.','error');error=True
        try:valid_email=validate_email(email,check_deliverability=False).normalized
        except EmailNotValidError as e:flash(f"Invalid email: {e}",'error');error=True;valid_email=None
        if valid_email and User.query.filter_by(email=valid_email).first():flash('Email already registered.','warning');error=True
        if error:return render_template('signup.html',form_data=request.form)
        new_user=User(email=valid_email,name=name,phone=phone);new_user.set_password(password);db.session.add(new_user)
        try:db.session.commit();flash('Account created! Logged in.','success');login_user(new_user);return redirect(url_for('index'))
        except Exception as e:db.session.rollback();flash('DB error creating account.','error');print(f"Signup Error: {e}");return render_template('signup.html',form_data=request.form)
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    # ... same as previous ...
    if current_user.is_authenticated:return redirect(url_for('index'))
    if request.method=='POST':
        email=request.form.get('email','').strip();password=request.form.get('password');remember=True if request.form.get('remember')else False
        try:valid_email=validate_email(email,check_deliverability=False).normalized
        except EmailNotValidError:flash('Invalid email format.','error');return render_template('login.html',form_data=request.form)
        user=User.query.filter_by(email=valid_email).first()
        if not user or not user.check_password(password):flash('Invalid email or password.','error');return render_template('login.html',form_data=request.form)
        login_user(user,remember=remember);flash('Logged in.','success')
        next_page=request.args.get('next');
        if next_page and not next_page.startswith('/'):next_page=url_for('index')
        return redirect(next_page or url_for('index'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    # ... same as previous ...
    logout_user();flash('Logged out.','success');return redirect(url_for('login'))


# --- Main Execution ---
if __name__ == '__main__':
    with app.app_context(): print("Initializing DB..."); db.create_all(); print("DB Initialized.")
    # Ensure the OPENAI_API_KEY environment variable is set before running
    if not os.environ.get("OPENAI_API_KEY"):
        print("CRITICAL ERROR: OPENAI_API_KEY environment variable not set.")
    else:
        print("OpenAI API Key found.")
    app.run(debug=True, port=5001)