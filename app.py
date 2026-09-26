import streamlit as st
import pandas as pd
import numpy as np
import datetime
import fitparse
import matplotlib.pyplot as plt
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="Garmin Workout Analyzer", layout="wide")

st.title("🏃‍♂️ Garmin Workout Analyzer (FIT Files)")
st.write("Upload your Garmin FIT files to view workout summaries, general comparison charts, or individual session analysis with interactive real maps.")

# File uploader
uploaded_files = st.file_uploader("Choose FIT Files", type=["fit", "FIT"], accept_multiple_files=True)

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
        df['timestamp'] = df['timestamp'] + pd.Timedelta(hours=3) # Israel Time (UTC+3)
    
    # Extract speed reliably (check 'enhanced_speed' first, then 'speed')
    if 'enhanced_speed' in df.columns and df['enhanced_speed'].notna().any():
        df['speed_kmh'] = df['enhanced_speed'] * 3.6
    elif 'speed' in df.columns and df['speed'].notna().any():
        df['speed_kmh'] = df['speed'] * 3.6
    else:
        df['speed_kmh'] = 0.0
        
    return df

if uploaded_files:
    st.success(f"{len(uploaded_files)} file(s) uploaded successfully!")
    
    workouts = []
    
    for f in uploaded_files:
        try:
            df = parse_fit_file(f.getvalue())
            if not df.empty and 'timestamp' in df.columns:
                start_time = df['timestamp'].iloc[0]
                act_id = f.name.split('_')[0]
                # Elapsed time calculations
                df['elapsed_sec'] = (df['timestamp'] - start_time).dt.total_seconds()
                df['elapsed_min'] = df['elapsed_sec'] / 60.0
                
                workouts.append({
                    'filename': f.name,
                    'act_id': act_id,
                    'start_time': start_time,
                    'df': df
                })
        except Exception as e:
            st.error(f"Error parsing file {f.name}: {e}")

    if workouts:
        # Chronological sort
        workouts = sorted(workouts, key=lambda x: x['start_time'])

        st.markdown("---")
        st.header("🌐 Comparative Charts (All Workouts)")
        
        col_gen1, col_gen2, col_gen3 = st.columns(3)
        
        # Initialize session states for comparative view
        if 'show_comp_hr' not in st.session_state: st.session_state.show_comp_hr = False
        if 'show_comp_speed' not in st.session_state: st.session_state.show_comp_speed = False
        if 'show_comp_map' not in st.session_state: st.session_state.show_comp_map = False

        if col_gen1.button("📊 Heart Rate Comparison"):
            st.session_state.show_comp_hr = not st.session_state.show_comp_hr

        if col_gen2.button("🚀 Speed Comparison"):
            st.session_state.show_comp_speed = not st.session_state.show_comp_speed

        if col_gen3.button("🗺️ Combined GPS Route Map"):
            st.session_state.show_comp_map = not st.session_state.show_comp_map

        # Render Comparative Charts based on State
        if st.session_state.show_comp_hr:
            fig, ax = plt.subplots(figsize=(10, 4))
            for w in workouts:
                df = w['df']
                if 'heart_rate' in df.columns and not df['heart_rate'].dropna().empty:
                    label_str = f"{w['start_time'].strftime('%Y-%m-%d')} ({w['act_id']})"
                    ax.plot(df['elapsed_min'], df['heart_rate'], label=label_str, alpha=0.8, linewidth=1.5)
            ax.set_title("Heart Rate Comparison Over Time", fontsize=12)
            ax.set_xlabel("Elapsed Time (minutes)", fontsize=10)
            ax.set_ylabel("Heart Rate (bpm)", fontsize=10)
            ax.grid(True, linestyle='--', alpha=0.5)
            ax.legend(loc='best', fontsize='small')
            st.pyplot(fig)

        if st.session_state.show_comp_speed:
            fig, ax = plt.subplots(figsize=(10, 4))
            for w in workouts:
                df = w['df']
                if 'speed_kmh' in df.columns and not df['speed_kmh'].dropna().empty:
                    label_str = f"{w['start_time'].strftime('%Y-%m-%d')} ({w['act_id']})"
                    ax.plot(df['elapsed_min'], df['speed_kmh'], label=label_str, alpha=0.8, linewidth=1.5)
            ax.set_title("Speed Comparison Over Time", fontsize=12)
            ax.set_xlabel("Elapsed Time (minutes)", fontsize=10)
            ax.set_ylabel("Speed (km/h)", fontsize=10)
            ax.grid(True, linestyle='--', alpha=0.5)
            ax.legend(loc='best', fontsize='small')
            st.pyplot(fig)

        if st.session_state.show_comp_map:
            gps_tracks = []
            colors = ['blue', 'red', 'green', 'purple', 'orange', 'darkred', 'darkblue', 'darkgreen', 'cadetblue', 'pink', 'black']
            
            for w in workouts:
                df = w['df']
                if 'position_lat' in df.columns and 'position_long' in df.columns:
                    map_df = df.dropna(subset=['position_lat', 'position_long']).copy()
                    if not map_df.empty:
                        lats = (map_df['position_lat'] * (180 / 2**31)).tolist()
                        lons = (map_df['position_long'] * (180 / 2**31)).tolist()
                        label_str = f"{w['start_time'].strftime('%Y-%m-%d')} ({w['act_id']})"
                        gps_tracks.append((label_str, list(zip(lats, lons))))
            
            if gps_tracks:
                all_lats = [pt[0] for track in gps_tracks for pt in track[1]]
                all_lons = [pt[1] for track in gps_tracks for pt in track[1]]
                center_lat = sum(all_lats) / len(all_lats)
                center_lon = sum(all_lons) / len(all_lons)
                
                m = folium.Map(location=[center_lat, center_lon], zoom_start=13, tiles="OpenStreetMap")
                for idx, (label, coords) in enumerate(gps_tracks):
                    color = colors[idx % len(colors)]
                    folium.PolyLine(coords, color=color, weight=3.5, opacity=0.85, popup=label, tooltip=label).add_to(m)
                
                st.subheader("All Workout Routes on Interactive Real Map")
                st_folium(m, width=900, height=500, key="combined_map")
            else:
                st.warning("No GPS data found in uploaded files.")

        st.markdown("---")
        st.subheader("📋 Workout List & Individual Sessions:")

        for i, w in enumerate(workouts):
            dt_str = w['start_time'].strftime('%Y-%m-%d at %H:%M')
            
            with st.expander(f"📌 Workout {i+1}: {dt_str} (ID: {w['act_id']})"):
                col1, col2, col3 = st.columns(3)
                
                # Session state keys for individual workouts
                key_hr = f"view_hr_{i}"
                key_map = f"view_map_{i}"
                key_stats = f"view_stats_{i}"
                
                if key_hr not in st.session_state: st.session_state[key_hr] = False
                if key_map not in st.session_state: st.session_state[key_map] = False
                if key_stats not in st.session_state: st.session_state[key_stats] = False

                if col1.button(f"📈 HR & Speed Chart", key=f"btn_hr_{i}"):
                    st.session_state[key_hr] = not st.session_state[key_hr]

                if col2.button(f"🗺️ Real Map Route", key=f"btn_map_{i}"):
                    st.session_state[key_map] = not st.session_state[key_map]

                if col3.button(f"📊 Session Stats", key=f"btn_stats_{i}"):
                    st.session_state[key_stats] = not st.session_state[key_stats]

                # Render Individual Items
                if st.session_state[key_hr]:
                    df = w['df']
                    fig, ax1 = plt.subplots(figsize=(10, 4))
                    
                    if 'heart_rate' in df.columns:
                        ax1.plot(df['elapsed_min'], df['heart_rate'], color='red', label='Heart Rate (bpm)', linewidth=1.2)
                        ax1.set_ylabel('Heart Rate (bpm)', color='red')
                        ax1.set_xlabel('Elapsed Time (min)')
                    
                    if 'speed_kmh' in df.columns and df['speed_kmh'].max() > 0:
                        ax2 = ax1.twinx()
                        ax2.plot(df['elapsed_min'], df['speed_kmh'], color='blue', alpha=0.6, label='Speed (km/h)', linewidth=1.2)
                        ax2.set_ylabel('Speed (km/h)', color='blue')
                        
                    plt.title(f"Heart Rate & Speed - {dt_str}")
                    st.pyplot(fig)

                if st.session_state[key_map]:
                    df = w['df']
                    if 'position_lat' in df.columns and 'position_long' in df.columns:
                        map_df = df.dropna(subset=['position_lat', 'position_long']).copy()
                        if not map_df.empty:
                            lats = (map_df['position_lat'] * (180 / 2**31)).tolist()
                            lons = (map_df['position_long'] * (180 / 2**31)).tolist()
                            coords = list(zip(lats, lons))
                            
                            center_lat = sum(lats) / len(lats)
                            center_lon = sum(lons) / len(lons)
                            
                            m_ind = folium.Map(location=[center_lat, center_lon], zoom_start=14, tiles="OpenStreetMap")
                            folium.PolyLine(coords, color='blue', weight=4, opacity=0.85, tooltip=f"Workout: {dt_str}").add_to(m_ind)
                            
                            st.subheader(f"Route Map - {dt_str}")
                            st_folium(m_ind, width=700, height=450, key=f"ind_map_{i}")
                    else:
                        st.warning("No GPS data found in this file.")

                if st.session_state[key_stats]:
                    df = w['df']
                    duration = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).total_seconds() / 60
                    max_speed = df['speed_kmh'].max() if 'speed_kmh' in df.columns else 0
                    avg_hr = df['heart_rate'].mean() if 'heart_rate' in df.columns else 0
                    
                    st.write(f"⏱️ **Duration:** {duration:.1f} min")
                    st.write(f"🚀 **Max Speed:** {max_speed:.2f} km/h")
                    st.write(f"❤️ **Avg Heart Rate:** {avg_hr:.0f} bpm")
