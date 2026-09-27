import streamlit as st
import pandas as pd
import numpy as np
import datetime
import fitparse
import gpxpy
import matplotlib.pyplot as plt
import folium
from streamlit_folium import st_folium
from garminconnect import Garmin
import zipfile
import io

st.set_page_config(page_title="Garmin Workout Analyzer", layout="wide")

st.title("🏃‍♂️ Garmin Workout Analyzer")
st.write("Analyze your workouts either by connecting directly to Garmin Connect or by uploading FIT/GPX files manually.")

# Initialize session state for workouts persistence across re-runs
if 'workouts' not in st.session_state:
    st.session_state.workouts = []
if 'available_activities' not in st.session_state:
    st.session_state.available_activities = []
if 'garmin_client' not in st.session_state:
    st.session_state.garmin_client = None

# Sidebar for Garmin Connect Login
st.sidebar.header("🔐 Garmin Connect Login")
email = st.sidebar.text_input("Garmin Email")
password = st.sidebar.text_input("Garmin Password", type="password")
login_btn = st.sidebar.button("Connect & List Workouts")

# Speed threshold for running vs. walking
RUN_SPEED_THRESHOLD_KMH = 6.5

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
    
    if 'enhanced_speed' in df.columns and df['enhanced_speed'].notna().any():
        df['speed_kmh'] = df['enhanced_speed'] * 3.6
    elif 'speed' in df.columns and df['speed'].notna().any():
        df['speed_kmh'] = df['speed'] * 3.6
    else:
        df['speed_kmh'] = 0.0
        
    # Extract altitude / elevation if available
    if 'enhanced_altitude' in df.columns and df['enhanced_altitude'].notna().any():
        df['elevation'] = df['enhanced_altitude']
    elif 'altitude' in df.columns and df['altitude'].notna().any():
        df['elevation'] = df['altitude']
    elif 'elevation' not in df.columns:
        df['elevation'] = np.nan
        
    return df

def parse_gpx_file(file_bytes):
    gpx_text = file_bytes.decode('utf-8', errors='ignore')
    gpx = gpxpy.parse(gpx_text)
    
    records = []
    for track in gpx.tracks:
        for segment in track.segments:
            for pt in segment.points:
                records.append({
                    'timestamp': pd.to_datetime(pt.time) + pd.Timedelta(hours=3) if pt.time else None,
                    'position_lat': pt.latitude * (2**31 / 180.0),
                    'position_long': pt.longitude * (2**31 / 180.0),
                    'lat': pt.latitude,
                    'lon': pt.longitude,
                    'elevation': pt.elevation,
                    'speed': pt.speed
                })
                
    df = pd.DataFrame(records)
    if not df.empty and 'timestamp' in df.columns and df['timestamp'].notna().any():
        df = df.dropna(subset=['timestamp']).reset_index(drop=True)
        if 'speed' in df.columns and df['speed'].notna().any():
            df['speed_kmh'] = df['speed'] * 3.6
        else:
            df['lat_rad'] = np.radians(df['lat'])
            df['lon_rad'] = np.radians(df['lon'])
            dlat = df['lat_rad'].diff()
            dlon = df['lon_rad'].diff()
            a = np.sin(dlat/2)**2 + np.cos(df['lat_rad'].shift()) * np.cos(df['lat_rad']) * np.sin(dlon/2)**2
            c = 2 * np.arcsin(np.sqrt(a))
            dist_m = c * 6371000.0
            dt_sec = df['timestamp'].diff().dt.total_seconds()
            df['speed_kmh'] = (dist_m / dt_sec) * 3.6
            df['speed_kmh'] = df['speed_kmh'].fillna(0).clip(lower=0, upper=80)
    else:
        df['speed_kmh'] = 0.0
        
    return df

# Connect to Garmin & Fetch list of last 10 activities
if login_btn:
    if email and password:
        try:
            with st.spinner("Connecting to Garmin Connect..."):
                client = Garmin(email, password)
                client.login()
                st.session_state.garmin_client = client
                activities = client.get_activities(0, 10) # Fetch last 10 activities
                st.session_state.available_activities = activities
                st.sidebar.success(f"Connected! Found {len(activities)} recent workouts.")
        except Exception as e:
            st.sidebar.error(f"Login failed: {e}")
    else:
        st.sidebar.warning("Please enter your email and password.")

# Selection box for activities
if st.session_state.available_activities:
    st.sidebar.markdown("---")
    st.sidebar.subheader("Select Workouts to Analyze")
    
    options = {}
    for act in st.session_state.available_activities:
        act_id = act['activityId']
        act_name = act.get('activityName', 'Workout')
        start_time_str = act.get('startTimeLocal', '')
        options[f"{start_time_str} - {act_name} ({act_id})"] = act
        
    selected_options = st.sidebar.multiselect("Choose Workouts", list(options.keys()), default=list(options.keys())[:3])
    download_btn = st.sidebar.button("Download & Analyze Selected")

    if download_btn and selected_options:
        client = st.session_state.garmin_client
        fetched_workouts = []
        
        with st.spinner("Downloading selected FIT files..."):
            for opt_key in selected_options:
                act = options[opt_key]
                act_id = act['activityId']
                try:
                    raw_data = client.download_activity(act_id, dl_fmt=client.ActivityDownloadFormat.ORIGINAL)
                    fit_data = raw_data
                    try:
                        with zipfile.ZipFile(io.BytesIO(raw_data)) as z:
                            fit_filename = [name for name in z.namelist() if name.lower().endswith('.fit')][0]
                            fit_data = z.read(fit_filename)
                    except zipfile.BadZipFile:
                        pass

                    df = parse_fit_file(fit_data)
                    if not df.empty and 'timestamp' in df.columns:
                        start_time = df['timestamp'].iloc[0]
                        df['elapsed_sec'] = (df['timestamp'] - start_time).dt.total_seconds()
                        df['elapsed_min'] = df['elapsed_sec'] / 60.0
                        
                        fetched_workouts.append({
                            'filename': f"{act_id}.fit",
                            'act_id': str(act_id),
                            'start_time': start_time,
                            'df': df
                        })
                except Exception as e:
                    st.error(f"Error downloading workout {act_id}: {e}")
                    
        st.session_state.workouts = fetched_workouts
        st.success(f"Successfully loaded {len(fetched_workouts)} workout(s)!")

uploaded_files = st.file_uploader("Or Upload FIT/GPX Files Manually", type=["fit", "FIT", "gpx", "GPX"], accept_multiple_files=True)

# Parse manually uploaded files
if uploaded_files:
    uploaded_workouts = []
    for f in uploaded_files:
        try:
            fn_lower = f.name.lower()
            if fn_lower.endswith('.gpx'):
                df = parse_gpx_file(f.getvalue())
            else:
                df = parse_fit_file(f.getvalue())
                
            if not df.empty and 'timestamp' in df.columns:
                start_time = df['timestamp'].iloc[0]
                act_id = f.name.split('.')[0].split('_')[0]
                df['elapsed_sec'] = (df['timestamp'] - start_time).dt.total_seconds()
                df['elapsed_min'] = df['elapsed_sec'] / 60.0
                
                uploaded_workouts.append({
                    'filename': f.name,
                    'act_id': act_id,
                    'start_time': start_time,
                    'df': df
                })
        except Exception as e:
            st.error(f"Error parsing file {f.name}: {e}")
    if uploaded_workouts:
        st.session_state.workouts = uploaded_workouts

workouts = st.session_state.workouts

if workouts:
    workouts = sorted(workouts, key=lambda x: x['start_time'])

    st.markdown("---")
    st.header("🌐 Comparative Charts (All Workouts)")
    
    col_gen1, col_gen2, col_gen3 = st.columns(3)
    
    if 'show_comp_hr' not in st.session_state: st.session_state.show_comp_hr = False
    if 'show_comp_speed' not in st.session_state: st.session_state.show_comp_speed = False
    if 'show_comp_map' not in st.session_state: st.session_state.show_comp_map = False

    if col_gen1.button("📊 Heart Rate Comparison"):
        st.session_state.show_comp_hr = not st.session_state.show_comp_hr

    if col_gen2.button("🚀 Speed Comparison"):
        st.session_state.show_comp_speed = not st.session_state.show_comp_speed

    if col_gen3.button("🗺️ Combined GPS Route Map"):
        st.session_state.show_comp_map = not st.session_state.show_comp_map

    if st.session_state.show_comp_hr:
        fig, ax = plt.subplots(figsize=(8, 4))
        has_hr = False
        for w in workouts:
            df = w['df']
            if 'heart_rate' in df.columns and not df['heart_rate'].dropna().empty:
                label_str = f"{w['start_time'].strftime('%Y-%m-%d')} ({w['act_id']})"
                ax.plot(df['elapsed_min'], df['heart_rate'], label=label_str, alpha=0.8, linewidth=1.5)
                has_hr = True
        if has_hr:
            ax.set_title("Heart Rate Comparison Over Time", fontsize=11)
            ax.set_xlabel("Elapsed Time (min)", fontsize=9)
            ax.set_ylabel("Heart Rate (bpm)", fontsize=9)
            ax.grid(True, linestyle='--', alpha=0.5)
            ax.legend(loc='best', fontsize='small')
            plt.tight_layout()
            st.pyplot(fig, use_container_width=True)
        else:
            st.warning("No Heart Rate data found in the workouts.")

    if st.session_state.show_comp_speed:
        fig, ax = plt.subplots(figsize=(8, 4))
        for w in workouts:
            df = w['df']
            if 'speed_kmh' in df.columns and not df['speed_kmh'].dropna().empty:
                label_str = f"{w['start_time'].strftime('%Y-%m-%d')} ({w['act_id']})"
                ax.plot(df['elapsed_min'], df['speed_kmh'], label=label_str, alpha=0.8, linewidth=1.5)
        ax.set_title("Speed Comparison Over Time", fontsize=11)
        ax.set_xlabel("Elapsed Time (min)", fontsize=9)
        ax.set_ylabel("Speed (km/h)", fontsize=9)
        ax.grid(True, linestyle='--', alpha=0.5)
        ax.legend(loc='best', fontsize='small')
        plt.tight_layout()
        st.pyplot(fig, use_container_width=True)

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
            
            min_lat, max_lat = min(all_lats), max(all_lats)
            min_lon, max_lon = min(all_lons), max(all_lons)
            
            m = folium.Map(tiles="OpenStreetMap")
            for idx, (label, coords) in enumerate(gps_tracks):
                color = colors[idx % len(colors)]
                folium.PolyLine(coords, color=color, weight=3.5, opacity=0.85, popup=label, tooltip=label).add_to(m)
            
            m.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]], padding=(15, 15))
            
            st.subheader("All Workout Routes on Interactive Real Map")
            st_folium(m, use_container_width=True, height=400, key="combined_map")
        else:
            st.warning("No GPS data found in workouts.")

    st.markdown("---")
    st.subheader("📋 Workout List & Individual Sessions:")

    for i, w in enumerate(workouts):
        dt_str = w['start_time'].strftime('%Y-%m-%d at %H:%M')
        file_ext = w['filename'].split('.')[-1].upper()
        
        with st.expander(f"📌 Workout {i+1}: {dt_str} (ID: {w['act_id']}) [{file_ext}]"):
            col1, col2, col3, col4 = st.columns(4)
            
            key_hr = f"view_hr_{i}"
            key_map = f"view_map_{i}"
            key_stats = f"view_stats_{i}"
            key_elev = f"view_elev_{i}"
            
            if key_hr not in st.session_state: st.session_state[key_hr] = False
            if key_map not in st.session_state: st.session_state[key_map] = False
            if key_stats not in st.session_state: st.session_state[key_stats] = False
            if key_elev not in st.session_state: st.session_state[key_elev] = False

            if col1.button(f"📈 HR & Speed", key=f"btn_hr_{i}"):
                st.session_state[key_hr] = not st.session_state[key_hr]

            if col2.button(f"🗺️ Map Route", key=f"btn_map_{i}"):
                st.session_state[key_map] = not st.session_state[key_map]

            if col3.button(f"⛰️ Speed vs. Elevation (Running Only)", key=f"btn_elev_{i}"):
                st.session_state[key_elev] = not st.session_state[key_elev]

            if col4.button(f"📊 Stats", key=f"btn_stats_{i}"):
                st.session_state[key_stats] = not st.session_state[key_stats]

            if st.session_state[key_hr]:
                df = w['df']
                fig, ax1 = plt.subplots(figsize=(8, 3.5))
                
                if 'heart_rate' in df.columns and df['heart_rate'].notna().any():
                    ax1.plot(df['elapsed_min'], df['heart_rate'], color='red', label='Heart Rate (bpm)', linewidth=1.2)
                    ax1.set_ylabel('Heart Rate (bpm)', color='red', fontsize=9)
                    ax1.set_xlabel('Elapsed Time (min)', fontsize=9)
                else:
                    ax1.set_xlabel('Elapsed Time (min)', fontsize=9)
                
                if 'speed_kmh' in df.columns and df['speed_kmh'].max() > 0:
                    ax2 = ax1.twinx()
                    ax2.plot(df['elapsed_min'], df['speed_kmh'], color='blue', alpha=0.6, label='Speed (km/h)', linewidth=1.2)
                    ax2.set_ylabel('Speed (km/h)', color='blue', fontsize=9)
                    
                plt.title(f"Heart Rate & Speed - {dt_str}", fontsize=10)
                plt.tight_layout()
                st.pyplot(fig, use_container_width=True)

            if st.session_state[key_map]:
                df = w['df']
                if 'position_lat' in df.columns and 'position_long' in df.columns:
                    map_df = df.dropna(subset=['position_lat', 'position_long']).copy()
                    if not map_df.empty:
                        lats = (map_df['position_lat'] * (180 / 2**31)).tolist()
                        lons = (map_df['position_long'] * (180 / 2**31)).tolist()
                        coords = list(zip(lats, lons))
                        
                        min_lat, max_lat = min(lats), max(lats)
                        min_lon, max_lon = min(lons), max(lons)
                        
                        m_ind = folium.Map(tiles="OpenStreetMap")
                        folium.PolyLine(coords, color='blue', weight=4, opacity=0.85, tooltip=f"Workout: {dt_str}").add_to(m_ind)
                        m_ind.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]], padding=(15, 15))
                        
                        st.subheader(f"Route Map - {dt_str}")
                        st_folium(m_ind, use_container_width=True, height=350, key=f"ind_map_{i}")
                else:
                    st.warning("No GPS data found in this file.")

            # Speed vs. Elevation Chart (Filtered strictly for Running >= 6.5 km/h)
            if st.session_state[key_elev]:
                df = w['df']
                if 'elevation' in df.columns and 'speed_kmh' in df.columns and df['elevation'].notna().any():
                    run_df = df[df['speed_kmh'] >= RUN_SPEED_THRESHOLD_KMH].dropna(subset=['elevation', 'speed_kmh'])
                    
                    if not run_df.empty:
                        fig, ax = plt.subplots(figsize=(8, 3.5))
                        ax.scatter(run_df['elevation'], run_df['speed_kmh'], color='purple', alpha=0.6, edgecolors='none', s=15)
                        ax.set_title(f"Speed vs. Elevation (Running Only ≥ 6.5 km/h) - {dt_str}", fontsize=10)
                        ax.set_xlabel("Elevation / Altitude (m)", fontsize=9)
                        ax.set_ylabel("Running Speed (km/h)", fontsize=9)
                        ax.grid(True, linestyle='--', alpha=0.5)
                        plt.tight_layout()
                        st.pyplot(fig, use_container_width=True)
                    else:
                        st.warning("No running data found above 6.5 km/h with elevation details in this workout.")
                else:
                    st.warning("Elevation/Altitude data is not available for this file.")

            if st.session_state[key_stats]:
                df = w['df']
                duration = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).total_seconds() / 60
                
                run_df = df[df['speed_kmh'] >= RUN_SPEED_THRESHOLD_KMH]
                walk_df = df[df['speed_kmh'] < RUN_SPEED_THRESHOLD_KMH]
                
                run_time_min = len(run_df) / 60.0
                walk_time_min = len(walk_df) / 60.0
                
                avg_run_speed = run_df['speed_kmh'].mean() if not run_df.empty else 0.0
                avg_walk_speed = walk_df['speed_kmh'].mean() if not walk_df.empty else 0.0
                max_speed = df['speed_kmh'].max() if 'speed_kmh' in df.columns else 0
                avg_hr = df['heart_rate'].mean() if 'heart_rate' in df.columns and df['heart_rate'].notna().any() else None
                
                st.write(f"⏱️ **Total Duration:** {duration:.1f} min")
                st.write(f"🏃‍♂️ **Running Time (≥ 6.5 km/h):** {run_time_min:.1f} min (Avg Speed: {avg_run_speed:.2f} km/h)")
                st.write(f"🚶‍♂️ **Walking Time (< 6.5 km/h):** {walk_time_min:.1f} min (Avg Speed: {avg_walk_speed:.2f} km/h)")
                st.write(f"🚀 **Max Speed:** {max_speed:.2f} km/h")
                if avg_hr is not None:
                    st.write(f"❤️ **Avg Heart Rate:** {avg_hr:.0f} bpm")
                else:
                    st.write("❤️ **Avg Heart Rate:** N/A")
