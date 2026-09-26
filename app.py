import streamlit as st
import pandas as pd
import numpy as np
import datetime
import fitparse
import matplotlib.pyplot as plt

st.set_page_config(page_title="מנתח אימוני Garmin", layout="wide")

st.title("🏃‍♂️ מנתח קובצי אימון Garmin (FIT)")
st.write("העלה את קובצי ה-FIT שלך כדי לראות את רשימת המסלולים, להציג גרפים השוואתיים או גרפים נפרדים בלחיצת כפתור.")

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
        df['timestamp'] = df['timestamp'] + pd.Timedelta(hours=3) # שעון ישראל
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
                # חישוב זמן מתחילת האימון בדקות
                df['elapsed_sec'] = (df['timestamp'] - start_time).dt.total_seconds()
                df['elapsed_min'] = df['elapsed_sec'] / 60.0
                
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

        st.markdown("---")
        st.header("🌐 גרפים השוואתיים לכל האימונים (אחד על השני)")
        
        col_gen1, col_gen2, col_gen3 = st.columns(3)
        
        # 1. גרף השוואת דופק מרוכז
        if col_gen1.button("📊 השוואת דופק (כל האימונים)"):
            fig, ax = plt.subplots(figsize=(10, 5))
            for w in workouts:
                df = w['df']
                if 'heart_rate' in df.columns and not df['heart_rate'].dropna().empty:
                    label_str = f"{w['start_time'].strftime('%d/%m/%Y')} ({w['act_id']})"
                    ax.plot(df['elapsed_min'], df['heart_rate'], label=label_str, alpha=0.8, linewidth=1.5)
            
            ax.set_title("השוואת דופק לאורך זמן (Heart Rate Comparison)", fontsize=12)
            ax.set_xlabel("זמן מתחילת האימון (דקות)", fontsize=10)
            ax.set_ylabel("דופק (bpm)", fontsize=10)
            ax.grid(True, linestyle='--', alpha=0.5)
            ax.legend(loc='best', fontsize='small')
            st.pyplot(fig)

        # 2. גרף השוואת מהירות מרוכז
        if col_gen2.button("🚀 השוואת מהירות (כל האימונים)"):
            fig, ax = plt.subplots(figsize=(10, 5))
            for w in workouts:
                df = w['df']
                if 'speed_kmh' in df.columns and not df['speed_kmh'].dropna().empty:
                    label_str = f"{w['start_time'].strftime('%d/%m/%Y')} ({w['act_id']})"
                    ax.plot(df['elapsed_min'], df['speed_kmh'], label=label_str, alpha=0.8, linewidth=1.5)
            
            ax.set_title("השוואת מהירות לאורך זמן (Speed Comparison)", fontsize=12)
            ax.set_xlabel("זמן מתחילת האימון (דקות)", fontsize=10)
            ax.set_ylabel("מהירות (קמ\"ש)", fontsize=10)
            ax.grid(True, linestyle='--', alpha=0.5)
            ax.legend(loc='best', fontsize='small')
            st.pyplot(fig)

        # 3. מפת מסלולי GPS מאוחדת לכל האימונים (קווים דקים ללא נקודות)
        if col_gen3.button("🗺️ מפת כל המסלולים (GPS Combined)"):
            fig, ax = plt.subplots(figsize=(10, 6))
            has_gps = False
            for w in workouts:
                df = w['df']
                if 'position_lat' in df.columns and 'position_long' in df.columns:
                    map_df = df.dropna(subset=['position_lat', 'position_long']).copy()
                    if not map_df.empty:
                        lats = map_df['position_lat'] * (180 / 2**31)
                        lons = map_df['position_long'] * (180 / 2**31)
                        label_str = f"{w['start_time'].strftime('%d/%m/%Y')} ({w['act_id']})"
                        ax.plot(lons, lats, label=label_str, linewidth=1.2, alpha=0.8)
                        has_gps = True
            
            if has_gps:
                ax.set_title("מפת מסלולי GPS של כל האימונים", fontsize=12)
                ax.set_xlabel("Longitude (°)", fontsize=10)
                ax.set_ylabel("Latitude (°)", fontsize=10)
                ax.grid(True, linestyle='--', alpha=0.5)
                ax.legend(loc='best', fontsize='small')
                st.pyplot(fig)
            else:
                st.warning("לא נמצאו נתוני GPS בקבצים אלו.")

        st.markdown("---")
        st.subheader("📋 רשימת מסלולים ואימונים נפרדים:")

        for i, w in enumerate(workouts):
            dt_str = w['start_time'].strftime('%d/%m/%Y בשעה %H:%M')
            
            with st.expander(f"📌 אימון {i+1}: {dt_str} (מזהה: {w['act_id']})"):
                col1, col2, col3 = st.columns(3)
                
                # כפתור 1: גרף דופק ומהירות אישי
                if col1.button(f"📈 גרף דופק ומהירות", key=f"hr_{i}"):
                    df = w['df']
                    fig, ax1 = plt.subplots(figsize=(10, 4))
                    
                    if 'heart_rate' in df.columns:
                        ax1.plot(df['elapsed_min'], df['heart_rate'], color='red', label='Heart Rate (bpm)', linewidth=1.2)
                        ax1.set_ylabel('Heart Rate (bpm)', color='red')
                        ax1.set_xlabel('זמן (דקות)')
                    
                    if 'speed_kmh' in df.columns:
                        ax2 = ax1.twinx()
                        ax2.plot(df['elapsed_min'], df['speed_kmh'], color='blue', alpha=0.6, label='Speed (km/h)', linewidth=1.2)
                        ax2.set_ylabel('Speed (km/h)', color='blue')
                        
                    plt.title(f"דופק ומהירות - {dt_str}")
                    st.pyplot(fig)

                # כפתור 2: מפת מסלול GPS אישית (קו דק חלק)
                if col2.button(f"🗺️ מפת מסלול (GPS)", key=f"map_{i}"):
                    df = w['df']
                    if 'position_lat' in df.columns and 'position_long' in df.columns:
                        map_df = df.dropna(subset=['position_lat', 'position_long']).copy()
                        if not map_df.empty:
                            lats = map_df['position_lat'] * (180 / 2**31)
                            lons = map_df['position_long'] * (180 / 2**31)
                            
                            fig, ax = plt.subplots(figsize=(8, 5))
                            ax.plot(lons, lats, color='#1f77b4', linewidth=1.5)
                            ax.set_title(f"מסלול GPS - {dt_str}")
                            ax.set_xlabel("Longitude (°)")
                            ax.set_ylabel("Latitude (°)")
                            ax.grid(True, linestyle='--', alpha=0.5)
                            st.pyplot(fig)
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
