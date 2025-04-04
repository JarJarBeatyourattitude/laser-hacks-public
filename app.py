import json
import os
import random
from datetime import datetime, timedelta, timezone # Import timezone for UTC

# Attempt to import dateutil, provide fallback/warning if unavailable
try:
    from dateutil import parser as dateutil_parser
    HAS_DATEUTIL = True
except ImportError:
    HAS_DATEUTIL = False
    print("WARNING: 'python-dateutil' library not found. Datetime parsing might be less robust.")
    print("Install using: pip install python-dateutil")

from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc, asc, nullslast # Import nullslast for sorting
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from email_validator import validate_email, EmailNotValidError
from openai import OpenAI
# from dotenv import load_dotenv

# load_dotenv()

# --- App Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-please-change')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# --- Database Models ---
# (Models remain unchanged)
class Comment(db.Model):
    __tablename__ = 'comment'
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False, index=True)
    def __repr__(self): return f'<Comment {self.id}>'

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    events = db.relationship('Event', backref='creator', lazy='dynamic', cascade="all, delete-orphan")
    commitments = db.relationship('Commitment', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='author', lazy='dynamic', cascade="all, delete-orphan")
    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)
    def __repr__(self): return f'<User {self.email}>'

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
    commitments = db.relationship('Commitment', backref='event', cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='event', cascade="all, delete-orphan", order_by=Comment.created_at.asc())
    def __repr__(self): return f'<Event {self.id}: {self.subject}>'

class Commitment(db.Model):
    __tablename__ = 'commitment'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False, index=True)
    committed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint('user_id', 'event_id', name='_user_event_uc'),)
    def __repr__(self): return f'<Commitment User:{self.user_id} to Event:{self.event_id}>'

# --- Flask-Login Setup ---
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- Helper Function for Datetime Parsing ---
# (Unchanged)
def parse_datetime_string(datetime_str):
    if not datetime_str: return None
    try: dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00')); dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt; return dt.astimezone(timezone.utc)
    except ValueError:
        if HAS_DATEUTIL:
            try: dt = dateutil_parser.parse(datetime_str, fuzzy=False); dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt; return dt.astimezone(timezone.utc)
            except Exception as e: print(f"dateutil parse failed: {e}"); return None
        else: print(f"ISO parse failed: {datetime_str}"); return None
    except Exception as e: print(f"Generic parse error: {e}"); return None

# --- OpenAI LLM Interaction ---
# (Unchanged)
def call_openai_llm(student_data, user_name="Student"):
    print(f"--- Calling OpenAI API for user: {user_name} ---")
    api_key = os.environ.get("OPENAI_API_KEY");
    if not api_key: raise ValueError("OpenAI API key not found")
    try: client = OpenAI(api_key=api_key)
    except Exception as e: raise ValueError(f"Failed to init OpenAI client: {e}")
    now_utc = datetime.now(timezone.utc); current_time_str = now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
    system_prompt = """[PROMPT TRUNCATED - Same as previous version asking for JSON list and ISO datetime]""".format(user_name=user_name, current_time=current_time_str)
    user_prompt = f"Student info:\n{json.dumps(student_data)}"
    try:
        print(f"Sending request to OpenAI API (Current Time: {current_time_str})...")
        response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"system","content":system_prompt},{"role":"user","content":user_prompt}], response_format={"type":"json_object"})
        print("Received response..."); json_string = response.choices[0].message.content; print(f"Raw Response:\n{json_string[:300]}...") # Truncate long raw response
        parsed_response = json.loads(json_string)
        petitions_list = []
        if isinstance(parsed_response, list): petitions_list = parsed_response
        elif isinstance(parsed_response, dict):
            found_list = False
            for value in parsed_response.values():
                if isinstance(value, list): petitions_list = value; found_list = True; print("Parsed list nested in dict."); break
            if not found_list:
                 required_keys_check = ["type", "subject", "details", "proposed_datetime", "proposed_location", "reasoning"]
                 if all(key in parsed_response for key in required_keys_check): print("API returned single object, treating as list."); petitions_list = [parsed_response]
                 else: print("Warning: API returned dict, but not single proposal or list wrapper.")
        else: print(f"Warning: Unexpected JSON structure: {type(parsed_response)}")
        valid_petitions=[]; required_keys=["type","subject","details","proposed_datetime","proposed_location","reasoning"]
        for item in petitions_list:
            if isinstance(item,dict) and all(key in item for key in required_keys): valid_petitions.append(item)
            else: print(f"Warning: Invalid proposal format: {item}")
        print(f"--- OpenAI OK. Valid Proposals: {len(valid_petitions)} ---"); return valid_petitions
    except json.JSONDecodeError as json_err: print(f"ERROR: Failed JSON decode: {json_err}"); print(f"Received text: {json_string[:500]}..."); raise ValueError("Invalid JSON from AI.")
    except Exception as e: print(f"ERROR: OpenAI call failed: {e}"); raise

# --- Routes ---

@app.route('/')
def index():
    """Displays existing events with filtering/sorting and user's committed events."""
    if not current_user.is_authenticated:
         flash('Please log in or sign up.', 'info'); return redirect(url_for('login'))

    subject_filter = request.args.get('subject', '').strip()
    sort_by = request.args.get('sort_by', 'event_time_asc') # Default sort

    # --- Query for All Events (Filtered/Sorted) ---
    events = [] # Default
    try:
        query = Event.query.options(
            db.joinedload(Event.creator),
            db.subqueryload(Event.comments).joinedload(Comment.author),
            db.subqueryload(Event.commitments).joinedload(Commitment.user)
        )
        if subject_filter: query = query.filter(Event.subject.ilike(f'%{subject_filter}%'))
        if sort_by == 'date_asc': query = query.order_by(Event.created_at.asc())
        elif sort_by == 'date_desc': query = query.order_by(Event.created_at.desc())
        elif sort_by == 'event_time_asc': query = query.order_by(nullslast(Event.proposed_datetime.asc()))
        else: query = query.order_by(nullslast(Event.proposed_datetime.asc())) # Default
        events = query.all()
    except Exception as e:
        print(f"Error fetching all events: {e}"); flash("Error loading events.", "error")

    # --- NEW: Query for User's Upcoming Committed Events ---
    committed_events = [] # Default
    now_utc = datetime.now(timezone.utc)
    try:
        committed_events = Event.query.join(Commitment)\
            .filter(Commitment.user_id == current_user.id)\
            .filter(Event.proposed_datetime.isnot(None))\
            .filter(Event.proposed_datetime > now_utc) \
            .order_by(Event.proposed_datetime.asc())\
            .all() # Get upcoming events user committed to, ordered by event time
    except Exception as e:
         print(f"Error fetching committed events: {e}"); flash("Error loading your committed events.", "warning")
         # Don't necessarily stop the whole page from loading

    current_filters = { 'subject': subject_filter, 'sort_by': sort_by }
    # Pass both lists to the template
    return render_template('index.html',
                           events=events,
                           committed_events=committed_events, # Add committed events list
                           filters=current_filters)

# (Calendar routes unchanged)
@app.route('/calendar')
@login_required
def calendar_view(): return render_template('calendar.html')

@app.route('/api/events_for_calendar')
@login_required
def api_events_for_calendar():
    try:
        events = Event.query.filter(Event.proposed_datetime.isnot(None)).all(); calendar_events = []
        for event in events:
            start_time = event.proposed_datetime.isoformat()
            event_url = url_for('index', _anchor=f'event-{event.id}', _external=False)
            calendar_events.append({'id': event.id, 'title': event.subject, 'start': start_time, 'url': event_url, 'description': event.details or '', 'location': event.proposed_location or ''})
        return jsonify(calendar_events)
    except Exception as e: print(f"Error calendar API: {e}"); return jsonify({"error": "Fetch error"}), 500

# (Propose route unchanged)
@app.route('/propose', methods=['GET', 'POST'])
@login_required
def propose_event_form():
    if request.method == 'POST':
        classes=request.form.get('classes','').strip();availability=request.form.get('availability','').strip();purpose=request.form.get('purpose','').strip()
        if not availability or not purpose: flash('Purpose and availability required.','error'); return render_template('propose_form.html',form_data=request.form)
        student_input_data={"classes":classes,"availability":availability,"purpose":purpose}
        try:
            user_identifier=current_user.name or current_user.email; proposed_events_data=call_openai_llm(student_input_data,user_identifier)
            if not proposed_events_data: flash('AI suggestions failed/invalid.','info'); return render_template('propose_form.html',form_data=request.form)
            num_saved=0
            for event_data in proposed_events_data:
                proposed_dt_str=event_data.get('proposed_datetime'); parsed_datetime=parse_datetime_string(proposed_dt_str)
                if not parsed_datetime: flash(f"Warn: Bad time '{proposed_dt_str}' for '{event_data.get('subject')}'.",'warning')
                try: new_event=Event(user_id=current_user.id,subject=event_data.get('subject'),type=event_data.get('type'),proposed_time_string=proposed_dt_str,proposed_datetime=parsed_datetime,proposed_location=event_data.get('proposed_location'),details=event_data.get('details'),reasoning=event_data.get('reasoning'),status='proposed'); db.session.add(new_event); num_saved+=1
                except Exception as e: print(f"Error creating event: {e}"); flash("Warn: Couldn't save suggestion.","warning")
            if num_saved>0: db.session.commit(); flash(f'{num_saved} proposal(s) created!','success'); return redirect(url_for('index'))
            else: flash('Could not save valid suggestions.','error'); return render_template('propose_form.html',form_data=request.form)
        except ValueError as ve: flash(f'Config Error: {ve}','error')
        except Exception as e: db.session.rollback(); flash('Unexpected error during proposal.','error'); print(f"Error in /propose: {e}")
        return render_template('propose_form.html',form_data=request.form)
    return render_template('propose_form.html')

# (Commit route unchanged)
@app.route('/commit/<int:event_id>', methods=['POST'])
@login_required
def commit_to_event(event_id):
    event = db.session.get(Event, event_id);
    if not event: return jsonify({"error": "Event not found"}), 404
    if Commitment.query.filter_by(user_id=current_user.id, event_id=event_id).first(): return jsonify({"error": "Already committed"}), 409
    try: db.session.add(Commitment(user_id=current_user.id, event_id=event_id)); db.session.commit(); user_contact = current_user.phone or current_user.email; print(f"--- REMINDER SIM: User {current_user.email} committed to '{event.subject}'. Contact: {user_contact} ---"); return jsonify({"message": "Committed", "user_name": current_user.name or current_user.email}), 201
    except Exception as e: db.session.rollback(); print(f"Error commit: {e}"); return jsonify({"error": "DB error"}), 500

# (Add Comment route unchanged)
@app.route('/event/<int:event_id>/comment', methods=['POST'])
@login_required
def add_comment(event_id):
    event = db.session.get(Event, event_id);
    if not event: flash("Event not found.", "error"); return redirect(url_for('index'))
    comment_text = request.form.get('comment_text', '').strip()
    if not comment_text: flash("Comment cannot be empty.", "error"); return redirect(url_for('index', _anchor=f'event-{event_id}'))
    try: new_comment = Comment(text=comment_text, user_id=current_user.id, event_id=event_id); db.session.add(new_comment); db.session.commit(); flash("Comment added!", "success")
    except Exception as e: db.session.rollback(); print(f"Error adding comment: {e}"); flash("Error adding comment.", "error")
    return redirect(url_for('index', _anchor=f'event-{event_id}'))

# --- Authentication Routes ---
# (Unchanged)
@app.route('/signup', methods=['GET', 'POST'])
def signup():
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
    logout_user();flash('Logged out.','success');return redirect(url_for('login'))

# --- Main Execution ---
if __name__ == '__main__':
    with app.app_context(): print("Initializing DB..."); db.create_all(); print("DB Initialized.")
    app.run(debug=True, port=5001)