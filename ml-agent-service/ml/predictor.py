"""
Loads the trained models once at import time — training automatically
on first run if they don't exist yet, so there's no separate manual
"train before you start the server" step. Exposes one predict()
function the agent calls directly, same process, no HTTP.
"""
import pandas as pd
import joblib
from ml import train

if not train.models_exist():
    train.train_and_save()

_duration_model = joblib.load(train.DURATION_MODEL_PATH)
_delay_model = joblib.load(train.DELAY_MODEL_PATH)
_feature_columns = joblib.load(train.FEATURE_COLUMNS_PATH)


def predict(activity_type, venue, scheduled_duration, num_people,
            time_of_day, stakeholder_past_delay_rate, day_of_event, is_weekend):
    raw = pd.DataFrame([{
        "scheduled_duration": scheduled_duration, "num_people": num_people,
        "time_of_day": time_of_day, "stakeholder_past_delay_rate": stakeholder_past_delay_rate,
        "day_of_event": day_of_event, "is_weekend": is_weekend,
        "activity_type": activity_type, "venue": venue,
    }])
    encoded = pd.get_dummies(raw, columns=["activity_type", "venue"])
    # Same reindex-to-training-columns step as the original script —
    # a mismatch here silently produces garbage, not an error.
    encoded = encoded.reindex(columns=_feature_columns, fill_value=0)

    return {
        "predicted_duration_minutes": round(float(_duration_model.predict(encoded)[0])),
        "delay_probability": round(float(_delay_model.predict_proba(encoded)[0][1]), 2),
    }