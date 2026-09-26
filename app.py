import streamlit as st
import pandas as pd
import datetime
import fitparse
import matplotlib.pyplot as plt

st.set_page_config(page_title="מנתח אימוני Garmin", layout="wide")

st.title("🏃‍♂️ מנתח קובצי אימון Garmin (FIT)")
st.write("העלה את קובצי ה-FIT שלך כדי לראות את רשימת המסלולים ולייצר גרפים בלחיצת כפתור.")

# העלאת קבצים
uploaded_files = st.file_uploader("בחר קובצי FIT", type=["fit", "FIT"], accept_multiple_files=True)

def parse_fit_file(file_bytes):
    fitfile = fitparse.FitFile(file_bytes)
    data = []
    for record in fitfile.get_messages('record'):
        r_data = {}
        for data_entry in record:
            r_data[data_entry.name] = data_entry.value
        data.append(r_data)
    df = pd.DataFrame(data)
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        # המרה לשעון ישראל (UTC+3)
        df['timestamp'] = df['timestamp'] + pd.Timedelta(hours=3)
    if 'speed' in df.columns:
        df['speed_kmh'] = df['speed'] * 3.6
    return df

if uploaded_files:
    st.success(f"הועלו {len(uploaded_files)} קבצים בהצלחה!")
    
    workouts = []
    
    for f in uploaded_files:
        try:
            df = parse_fit_file(f.getvalue())
            if not df.empty and 'timestamp' in df.columns:
                start_time = df['timestamp'].iloc[0]
                act_id = f.name.split('_')[0]
                workouts.append({
                    'filename': f.name,
                    'act_id': act_id,
                    'start_time': start_time,
                    'df': df
                })
        except Exception as e:
            st.error(f"שגיאה בקריאת הקובץ {f.name}: {e}")

    if workouts:
        # מיון כרונולוגי של האימונים
        workouts = sorted(workouts, key=lambda x: x['start_time'])

        st.subheader("📋 רשימת מסלולים ואימונים שנמצאו:")

        for i, w in enumerate(workouts):
            dt_str = w['start_time'].strftime('%d/%m/%Y בשעה %H:%M')
            
            with st.expander(f"📌 אימון {i+1}: {dt_str} (מזהה: {w['act_id']})"):
                col1, col2, col3 = st.columns(3)
                
                # כפתור 1: גרף דופק ומהירות
                if col1.button(f"📈 גרף דופק ומהירות", key=f"hr_{i}"):
                    df = w['df']
                    fig, ax1 = plt.subplots(figsize=(10, 4))
                    
                    if 'heart_rate' in df.columns:
                        ax1.plot(df['timestamp'], df['heart_rate'], color='red', label='Heart Rate (bpm)')
                        ax1.set_ylabel('Heart Rate (bpm)', color='red')
                    
                    if 'speed_kmh' in df.columns:
                        ax2 = ax1.twinx()
                        ax2.plot(df['timestamp'], df['speed_kmh'], color='blue', alpha=0.6, label='Speed (km/h)')
                        ax2.set_ylabel('Speed (km/h)', color='blue')
                        
                    plt.title(f"דופק ומהירות - {dt_str}")
                    st.pyplot(fig)

                # כפתור 2: מפת מסלול GPS
                if col2.button(f"🗺️ מפת מסלול (GPS)", key=f"map_{i}"):
                    df = w['df']
                    if 'position_lat' in df.columns and 'position_long' in df.columns:
                        # המרה מקואורדינטות Semicircles למעלות רגילות
                        map_df = df.dropna(subset=['position_lat', 'position_long']).copy()
                        map_df['lat'] = map_df['position_lat'] * (180 / 2**31)
                        map_df['lon'] = map_df['position_long'] * (180 / 2**31)
                        st.map(map_df[['lat', 'lon']])
                    else:
                        st.warning("לא נמצאו נתוני GPS בקובץ זה.")

                # כפתור 3: נתונים סטטיסטיים
                if col3.button(f"📊 נתוני אימון", key=f"stats_{i}"):
                    df = w['df']
                    duration = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).total_seconds() / 60
                    max_speed = df['speed_kmh'].max() if 'speed_kmh' in df.columns else 0
                    avg_hr = df['heart_rate'].mean() if 'heart_rate' in df.columns else 0
                    
                    st.write(f"⏱️ **משך אימון:** {duration:.1f} דקות")
                    st.write(f"🚀 **מהירות שיא:** {max_speed:.2f} קמ\"ש")
                    st.write(f"❤️ **דופק ממוצע:** {avg_hr:.0f} bpm")