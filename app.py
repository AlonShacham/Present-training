import streamlit as st
import pandas as pd
import numpy as np
import datetime
import fitparse
import gpxpy
import folium
from streamlit_folium import st_folium
from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
import zipfile
import io
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# --- Page Config & Modern Dark Theme Custom CSS ---
st.set_page_config(
    page_title="Garmin Workout Analyzer",
    page_icon="🏃‍♂️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS Injector for Card-Based Dark Aesthetic
st.markdown("""
<style>
    /* Global Page Styling */
    .stApp {
        background-color: #0E1117;
        color: #E6EDF3;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    
    /* Card Container Styling */
    div[data-testid="stMetric"], .kpi-card {
        background-color: #161B22;
        border: 1px solid #30363D;
        border-radius: 12px;
        padding: 16px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
    }
    
    /* Custom Badge */
    .status-badge-success {
        background-color: rgba(16, 185, 129, 0.15);
        color: #10B981;
        border: 1px solid #10B981;
        padding: 6px 12px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
        display: inline-block;
        margin-bottom: 12px;
    }
    
    /* Tab Styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 12px;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: #161B22;
        border-radius: 8px 8px 0px 0px;
        padding: 10px 20px;
        color: #8B949E;
        border: 1px solid #30363D;
        border-bottom: none;
    }
    .stTabs [aria-selected="true"] {
        background-color: #00D2FF !important;
        color: #0E1117 !important;
        font-weight: bold;
    }

    /* Touch-Friendly Button Target Area */
    .stButton > button {
        min-height: 44px;
        border-radius: 8px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# --- Session State Initialization ---
if 'workouts' not in st.session_state: st.session_state.workouts = []
if 'available_activities' not in st.session_state: st.session_state.available_activities = []
if 'garmin_client' not in st.session_state: st.session_state.garmin_client = None
if 'connected_email' not in st.session_state: st.session_state.connected_email = ""
if 'run_threshold_kmh' not in st.session_state: st.session_state.run_threshold_kmh = 6.5

# --- Color Constants & Map Settings ---
COLOR_HR = "#FF4B4B"        # Neon Red / Rose
COLOR_SPEED = "#00D2FF"     # Electric Cyan
COLOR_INCLINE = "#A855F7"   # Violet
COLOR_RUN = "#10B981"       # Emerald Green
COLOR_WALK = "#F59E0B"      # Amber Yellow

# Standard OpenStreetMap Tiles (Free, No API Key Required)
MAP_TILES = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

# --- Helper Functions & Analytics ---
def calculate_distance_and_incline(df):
    if 'position_lat' in df.columns and 'position_long' in df.columns:
        lats = np.radians(df['position_lat'] * (180 / 2**31))
        lons = np.radians(df['position_long'] * (180 / 2**31))
    elif 'lat' in df.columns and 'lon' in df.columns:
        lats = np.radians(df['lat'])
        lons = np.radians(df['lon'])
    else:
        df['incline_pct'] = np.nan
        df['dist_km'] = 0.0
        return df

    dlat = lats.diff()
    dlon = lons.diff()
    a = np.sin(dlat/2)**2 + np.cos(lats.shift()) * np.cos(lats) * np.sin(dlon/2)**2
    c = 2 * np.arcsin(np.sqrt(a))
    dist_step_m = c * 6371000.0
    df['dist_m'] = dist_step_m.fillna(0).cumsum()
    df['dist_km'] = df['dist_m'] / 1000.0

    if 'elevation' in df.columns and df['elevation'].notna().any():
        window = 5
        delta_elev = df['elevation'].shift(-window) - df['elevation'].shift(window)
        delta_dist = df['dist_m'].shift(-window) - df['dist_m'].shift(window)
        
        with np.errstate(divide='ignore', invalid='ignore'):
            incline_pct = (delta_elev / delta_dist) * 100.0
            incline_pct = np.where(delta_dist > 1.0, incline_pct, 0.0)
            incline_pct = np.clip(incline_pct, -30.0, 30.0)
            
        df['incline_pct'] = pd.Series(incline_pct).fillna(0.0)
    else:
        df['incline_pct'] = np.nan
        
    return df

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
        df['timestamp'] = df['timestamp'] + pd.Timedelta(hours=3) # Israel Time
    
    if 'enhanced_speed' in df.columns and df['enhanced_speed'].notna().any():
        df['speed_kmh'] = df['enhanced_speed'] * 3.6
    elif 'speed' in df.columns and df['speed'].notna().any():
        df['speed_kmh'] = df['speed'] * 3.6
    else:
        df['speed_kmh'] = 0.0
        
    if 'enhanced_altitude' in df.columns and df['enhanced_altitude'].notna().any():
        df['elevation'] = df['enhanced_altitude']
    elif 'altitude' in df.columns and df['altitude'].notna().any():
        df['elevation'] = df['altitude']
    elif 'elevation' not in df.columns:
        df['elevation'] = np.nan
        
    df = calculate_distance_and_incline(df)
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
            
        df = calculate_distance_and_incline(df)
    else:
        df['speed_kmh'] = 0.0
        
    return df

# --- Sidebar: Garmin API Authentication & File Upload ---
st.sidebar.title("🏃‍♂️ Garmin Hub")

if st.session_state.connected_email:
    st.sidebar.markdown(f'<div class="status-badge-success">🟢 Connected as {st.session_state.connected_email}</div>', unsafe_allow_html=True)
    if st.sidebar.button("🔒 Logout / Change Account"):
        st.session_state.connected_email = ""
        st.session_state.garmin_client = None
        st.session_state.available_activities = []
        st.rerun()
else:
    st.sidebar.subheader("🔐 Garmin Connect Login")
    email_input = st.sidebar.text_input("Garmin Email")
    password_input = st.sidebar.text_input("Garmin Password", type="password")
    login_btn = st.sidebar.button("Connect & List Workouts")

    if login_btn and email_input and password_input:
        try:
            with st.spinner("Authenticating with Garmin Connect..."):
                client = Garmin(email_input, password_input)
                client.login()
                st.session_state.garmin_client = client
                st.session_state.connected_email = email_input
                st.session_state.available_activities = client.get_activities(0, 20)
                st.rerun()
        except (GarminConnectAuthenticationError, GarminConnectTooManyRequestsError):
            st.sidebar.error("⚠️ Garmin חסם את ההתחברות האוטומטית (Cloudflare / 2FA). ניתן להעלות קובצי FIT/GPX ישירות למטה.")
        except Exception as e:
            st.sidebar.error(f"Login failed: {e}. ניתן להעלות קובצי FIT/GPX ישירות למטה.")

if st.session_state.available_activities:
    st.sidebar.markdown("---")
    st.sidebar.subheader("📌 Select Recent Workouts")
    
    options = {}
    for act in st.session_state.available_activities:
        act_id = act['activityId']
        act_name = act.get('activityName', 'Workout')
        start_time_str = act.get('startTimeLocal', '')
        dist_km = act.get('distance', 0) / 1000.0
        options[f"🏃‍♂️ {start_time_str} - {act_name} ({dist_km:.1f} km)"] = act
        
    selected_options = st.sidebar.multiselect("Choose Workouts (from last 20)", list(options.keys()), default=list(options.keys())[:3])
    download_btn = st.sidebar.button("Download & Analyze Selected")

    if download_btn and selected_options:
        client = st.session_state.garmin_client
        fetched_workouts = []
        progress_bar = st.sidebar.progress(0)
        
        for idx, opt_key in enumerate(selected_options):
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
                st.sidebar.error(f"Error downloading {act_id}: {e}")
            progress_bar.progress((idx + 1) / len(selected_options))
            
        st.session_state.workouts = fetched_workouts
        st.sidebar.success(f"Loaded {len(fetched_workouts)} workout(s)!")

st.sidebar.markdown("---")
st.sidebar.subheader("📁 העלאת קבצים ישירה")
uploaded_files = st.sidebar.file_uploader("Upload FIT/GPX Files", type=["fit", "FIT", "gpx", "GPX"], accept_multiple_files=True)

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
            st.sidebar.error(f"Error parsing {f.name}: {e}")
    if uploaded_workouts:
        st.session_state.workouts = uploaded_workouts

workouts = st.session_state.workouts

# --- Main App Layout: 3 Tabbed Workspace ---
tab1, tab2, tab3 = st.tabs([
    "📊 Tab 1: Overview & Comparative Analytics",
    "📌 Tab 2: Individual Session Deep-Dive",
    "⚙️ Tab 3: Settings & Thresholds"
])

# --- TAB 1: OVERVIEW & COMPARATIVE ANALYTICS ---
with tab1:
    if workouts:
        workouts = sorted(workouts, key=lambda x: x['start_time'])
        
        # Aggregate KPI Calculation
        total_dist_km = sum([w['df']['dist_km'].iloc[-1] if 'dist_km' in w['df'].columns and not w['df']['dist_km'].empty else 0 for w in workouts])
        total_dur_min = sum([(w['df']['timestamp'].iloc[-1] - w['df']['timestamp'].iloc[0]).total_seconds() / 60.0 for w in workouts])
        
        all_hrs = pd.concat([w['df']['heart_rate'].dropna() for w in workouts if 'heart_rate' in w['df'].columns])
        avg_hr_all = int(all_hrs.mean()) if not all_hrs.empty else 0
        
        all_speeds = pd.concat([w['df']['speed_kmh'].dropna() for w in workouts if 'speed_kmh' in w['df'].columns])
        max_speed_all = all_speeds.max() if not all_speeds.empty else 0.0

        # Executive KPI Cards Row
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("📂 Loaded Workouts", len(workouts))
        col2.metric("📏 Total Distance", f"{total_dist_km:.2f} km")
        col3.metric("⏱️ Cumulative Duration", f"{total_dur_min:.1f} min")
        col4.metric("❤️ Avg HR / Max Speed", f"{avg_hr_all} bpm / {max_speed_all:.1f} km/h")
        
        st.markdown("---")
        
        # 1. Comparative Heart Rate Plotly
        st.subheader("📊 Heart Rate Comparison Over Time")
        fig_hr = go.Figure()
        for w in workouts:
            df = w['df']
            if 'heart_rate' in df.columns and not df['heart_rate'].dropna().empty:
                label_str = f"{w['start_time'].strftime('%Y-%m-%d')} ({w['act_id']})"
                fig_hr.add_trace(go.Scatter(
                    x=df['elapsed_min'], y=df['heart_rate'],
                    mode='lines', name=label_str,
                    hovertemplate="Time: %{x:.1f} min<br>HR: %{y:.0f} bpm<extra></extra>"
                ))
        fig_hr.update_layout(
            template="plotly_dark", paper_bgcolor="#161B22", plot_bgcolor="#161B22",
            margin=dict(l=20, r=20, t=30, b=20), hovermode="x unified",
            xaxis_title="Elapsed Time (min)", yaxis_title="Heart Rate (bpm)"
        )
        st.plotly_chart(fig_hr, use_container_width=True)

        # 2. Comparative Speed Plotly
        st.subheader("🚀 Speed Comparison Over Time")
        fig_sp = go.Figure()
        for w in workouts:
            df = w['df']
            if 'speed_kmh' in df.columns and not df['speed_kmh'].dropna().empty:
                label_str = f"{w['start_time'].strftime('%Y-%m-%d')} ({w['act_id']})"
                fig_sp.add_trace(go.Scatter(
                    x=df['elapsed_min'], y=df['speed_kmh'],
                    mode='lines', name=label_str,
                    hovertemplate="Time: %{x:.1f} min<br>Speed: %{y:.1f} km/h<extra></extra>"
                ))
        fig_sp.update_layout(
            template="plotly_dark", paper_bgcolor="#161B22", plot_bgcolor="#161B22",
            margin=dict(l=20, r=20, t=30, b=20), hovermode="x unified",
            xaxis_title="Elapsed Time (min)", yaxis_title="Speed (km/h)"
        )
        st.plotly_chart(fig_sp, use_container_width=True)

        # 3. Comparative Speed vs. Incline Grade (%) Scatter
        st.subheader("⛰️ Running Speed vs. Incline Grade (%) (All Workouts)")
        fig_inc = go.Figure()
        has_inc = False
        for w in workouts:
            df = w['df']
            if 'incline_pct' in df.columns and 'speed_kmh' in df.columns:
                run_df = df[df['speed_kmh'] >= st.session_state.run_threshold_kmh].dropna(subset=['incline_pct', 'speed_kmh'])
                if not run_df.empty:
                    label_str = f"{w['start_time'].strftime('%Y-%m-%d')} ({w['act_id']})"
                    fig_inc.add_trace(go.Scatter(
                        x=run_df['incline_pct'], y=run_df['speed_kmh'],
                        mode='markers', name=label_str, marker=dict(size=5, opacity=0.6),
                        hovertemplate="Incline: %{x:.1f}%<br>Speed: %{y:.1f} km/h<extra></extra>"
                    ))
                    has_inc = True
        if has_inc:
            fig_inc.update_layout(
                template="plotly_dark", paper_bgcolor="#161B22", plot_bgcolor="#161B22",
                margin=dict(l=20, r=20, t=30, b=20),
                xaxis_title="Incline / Slope Grade (%)", yaxis_title="Running Speed (km/h)"
            )
            st.plotly_chart(fig_inc, use_container_width=True)
        else:
            st.info("No running incline data available across workouts.")

        # 4. Combined GPS Route Map
        st.subheader("🗺️ Combined GPS Route Map")
        gps_tracks = []
        colors = ['#D97706', '#DC2626', '#059669', '#7C3AED', '#2563EB', '#DB2777', '#4F46E5']
        
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
            
            avg_lat_comb = np.mean(all_lats)
            avg_lon_comb = np.mean(all_lons)
            
            m = folium.Map(location=[avg_lat_comb, avg_lon_comb], zoom_start=12, tiles=MAP_TILES, attr=MAP_ATTR)
            for idx, (label, coords) in enumerate(gps_tracks):
                color = colors[idx % len(colors)]
                folium.PolyLine(coords, color=color, weight=3.5, opacity=0.85, popup=label, tooltip=label).add_to(m)
            
            m.fit_bounds([[min(all_lats), min(all_lons)], [max(all_lats), max(all_lons)]], padding=(20, 20))
            st_folium(m, use_container_width=True, height=450, key="combined_map")
    else:
        st.info("👋 Welcome! Connect to Garmin Connect or upload FIT/GPX files from the sidebar to analyze your workouts.")

# --- TAB 2: INDIVIDUAL SESSION DEEP-DIVE ---
with tab2:
    if workouts:
        st.header("📌 Individual Workout Analysis")
        for i, w in enumerate(workouts):
            dt_str = w['start_time'].strftime('%Y-%m-%d at %H:%M')
            file_ext = w['filename'].split('.')[-1].upper()
            
            with st.expander(f"📌 Workout {i+1}: {dt_str} (ID: {w['act_id']}) [{file_ext}]", expanded=(i==0)):
                df = w['df']
                
                # Walk vs Run Segmentation Calculations
                run_df = df[df['speed_kmh'] >= st.session_state.run_threshold_kmh]
                walk_df = df[df['speed_kmh'] < st.session_state.run_threshold_kmh]
                
                run_time_min = len(run_df) / 60.0
                walk_time_min = len(walk_df) / 60.0
                duration_min = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).total_seconds() / 60.0
                dist_km = df['dist_km'].iloc[-1] if 'dist_km' in df.columns else 0.0

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("📏 Distance", f"{dist_km:.2f} km")
                c2.metric("⏱️ Duration", f"{duration_min:.1f} min")
                c3.metric("🏃‍♂️ Running Split", f"{run_time_min:.1f} min")
                c4.metric("🚶‍♂️ Walking Split", f"{walk_time_min:.1f} min")

                st.markdown("---")
                
                # Dual Axis Plotly Chart via make_subplots
                fig_ind = make_subplots(specs=[[{"secondary_y": True}]])
                
                if 'heart_rate' in df.columns:
                    fig_ind.add_trace(
                        go.Scatter(
                            x=df['elapsed_min'], y=df['heart_rate'],
                            name="Heart Rate (bpm)", line=dict(color=COLOR_HR, width=1.5),
                            hovertemplate="HR: %{y:.0f} bpm<extra></extra>"
                        ),
                        secondary_y=False
                    )
                if 'speed_kmh' in df.columns:
                    fig_ind.add_trace(
                        go.Scatter(
                            x=df['elapsed_min'], y=df['speed_kmh'],
                            name="Speed (km/h)", line=dict(color=COLOR_SPEED, width=1.5),
                            hovertemplate="Speed: %{y:.1f} km/h<extra></extra>"
                        ),
                        secondary_y=True
                    )
                
                fig_ind.update_layout(
                    template="plotly_dark", paper_bgcolor="#161B22", plot_bgcolor="#161B22",
                    title=f"Heart Rate & Speed Profile - {dt_str}",
                    margin=dict(l=20, r=20, t=40, b=20), hovermode="x unified"
                )
                fig_ind.update_xaxes(title_text="Elapsed Time (min)")
                fig_ind.update_yaxes(title_text="Heart Rate (bpm)", title_font=dict(color=COLOR_HR), tickfont=dict(color=COLOR_HR), secondary_y=False)
                fig_ind.update_yaxes(title_text="Speed (km/h)", title_font=dict(color=COLOR_SPEED), tickfont=dict(color=COLOR_SPEED), secondary_y=True)
                
                st.plotly_chart(fig_ind, use_container_width=True)

                col_map, col_scatter = st.columns(2)
                
                # Individual Route Map
                with col_map:
                    st.subheader("🗺️ GPS Route Map")
                    if 'position_lat' in df.columns and 'position_long' in df.columns:
                        map_df = df.dropna(subset=['position_lat', 'position_long']).copy()
                        if not map_df.empty:
                            lats = (map_df['position_lat'] * (180 / 2**31)).tolist()
                            lons = (map_df['position_long'] * (180 / 2**31)).tolist()
                            coords = list(zip(lats, lons))
                            
                            # חישוב נקודת מרכז מדויקת לאימון
                            avg_lat = np.mean(lats)
                            avg_lon = np.mean(lons)
                            
                            # יצירת המפה עם מיקום מרכזי
                            m_ind = folium.Map(location=[avg_lat, avg_lon], zoom_start=13, tiles=MAP_TILES, attr=MAP_ATTR)
                            folium.PolyLine(coords, color="#2563EB", weight=4, opacity=0.9).add_to(m_ind)
                            
                            # התאמת גבולות זום מדויקת
                            m_ind.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(20, 20))
                            
                            st_folium(m_ind, use_container_width=True, height=350, key=f"ind_map_{w['act_id']}_{i}")
                    else:
                        st.info("No GPS coordinates in file.")

                # Speed vs. Incline Scatter (Running Only)
                with col_scatter:
                    st.subheader("⛰️ Speed vs. Incline Grade (%)")
                    if 'incline_pct' in df.columns and not run_df.empty:
                        fig_run_inc = px.scatter(
                            run_df, x='incline_pct', y='speed_kmh',
                            labels={'incline_pct': 'Incline Grade (%)', 'speed_kmh': 'Running Speed (km/h)'},
                            color_discrete_sequence=[COLOR_INCLINE]
                        )
                        fig_run_inc.update_layout(
                            template="plotly_dark", paper_bgcolor="#161B22", plot_bgcolor="#161B22",
                            margin=dict(l=20, r=20, t=30, b=20)
                        )
                        st.plotly_chart(fig_run_inc, use_container_width=True)
                    else:
                        st.info("No running segments (≥ 6.5 km/h) with incline data.")
    else:
        st.info("No workouts loaded yet.")

# --- TAB 3: SETTINGS & THRESHOLDS ---
with tab3:
    st.header("⚙️ Platform Settings & Thresholds")
    st.markdown("Configure analytical thresholds and account session preferences.")
    
    new_threshold = st.slider(
        "🏃‍♂️ Running Speed Threshold (km/h)",
        min_value=4.0, max_value=12.0, value=st.session_state.run_threshold_kmh, step=0.5,
        help="Velocities above this limit are categorized as running, while lower speeds are classified as walking."
    )
    if new_threshold != st.session_state.run_threshold_kmh:
        st.session_state.run_threshold_kmh = new_threshold
        st.success(f"Running threshold updated to {new_threshold} km/h!")

    st.markdown("---")
    st.subheader("🔒 Garmin Session Overview")
    if st.session_state.connected_email:
        st.write(f"**Authenticated Account:** `{st.session_state.connected_email}`")
        st.write("**API Status:** Active 🟢")
    else:
        st.write("No active Garmin Connect API session.")
