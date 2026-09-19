"""
Same synthetic-data approach and same two models (duration regression,
delay-probability classification) as the standalone script from
earlier in the project — packaged as a module so predictor.py can
import and call it directly instead of running it as a script.
"""
import os
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
import joblib

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'saved')
FEATURE_COLUMNS_PATH = os.path.join(MODEL_DIR, 'feature_columns.joblib')
DURATION_MODEL_PATH = os.path.join(MODEL_DIR, 'duration_model.joblib')
DELAY_MODEL_PATH = os.path.join(MODEL_DIR, 'delay_model.joblib')


def _generate_synthetic_activities(n=3000, seed=42):
    rng = np.random.default_rng(seed)
    activity_types = ["match", "workshop", "session", "ceremony", "meeting"]
    venues = ["Main Hall", "Room A", "Room B", "Outdoor Ground", "Auditorium"]
    rows = []
    for _ in range(n):
        activity_type = rng.choice(activity_types)
        venue = rng.choice(venues)
        scheduled_duration = rng.integers(20, 120)
        num_people = rng.integers(2, 60)
        time_of_day = rng.integers(8, 21)
        stakeholder_past_delay_rate = rng.uniform(0, 1)
        day_of_event = rng.integers(1, 11)
        is_weekend = rng.choice([0, 1])

        delay_probability_true = np.clip(
            0.15 + 0.5 * stakeholder_past_delay_rate + 0.002 * num_people
            + 0.02 * day_of_event + (0.1 if activity_type == "ceremony" else 0),
            0, 0.95,
        )
        delayed = rng.random() < delay_probability_true
        overrun = rng.uniform(5, 40) if delayed else rng.uniform(-5, 10)
        type_factor = {"match": 1.1, "workshop": 1.3, "session": 1.0, "ceremony": 1.4, "meeting": 0.9}[activity_type]
        actual_duration = max(10, scheduled_duration * type_factor + overrun)

        rows.append({
            "activity_type": activity_type, "venue": venue,
            "scheduled_duration": scheduled_duration, "num_people": num_people,
            "time_of_day": time_of_day,
            "stakeholder_past_delay_rate": round(float(stakeholder_past_delay_rate), 2),
            "day_of_event": day_of_event, "is_weekend": int(is_weekend),
            "actual_duration": round(float(actual_duration), 1), "delayed": int(delayed),
        })
    return pd.DataFrame(rows)


def train_and_save():
    os.makedirs(MODEL_DIR, exist_ok=True)
    data = _generate_synthetic_activities()
    features_df = pd.get_dummies(data.drop(columns=["actual_duration", "delayed"]), columns=["activity_type", "venue"])

    X_train, X_test, y_dur_train, y_dur_test, y_delay_train, y_delay_test = train_test_split(
        features_df, data["actual_duration"], data["delayed"], test_size=0.2, random_state=42
    )

    duration_model = RandomForestRegressor(n_estimators=200, random_state=42)
    duration_model.fit(X_train, y_dur_train)
    delay_model = RandomForestClassifier(n_estimators=200, random_state=42)
    delay_model.fit(X_train, y_delay_train)

    joblib.dump(duration_model, DURATION_MODEL_PATH)
    joblib.dump(delay_model, DELAY_MODEL_PATH)
    joblib.dump(list(features_df.columns), FEATURE_COLUMNS_PATH)
    print(f"Trained on {len(X_train)} synthetic activities. Saved to {MODEL_DIR}")


def models_exist():
    return all(os.path.exists(p) for p in [DURATION_MODEL_PATH, DELAY_MODEL_PATH, FEATURE_COLUMNS_PATH])