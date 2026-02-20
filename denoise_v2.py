import streamlit as st
import os
import subprocess
import tempfile
import shutil
import warnings
import time
import torch
import torchaudio
import datetime

# 忽略警告
warnings.filterwarnings("ignore")

# 強制設定 Windows 音訊後端 (本機開發用，雲端 Linux 通常內建)
try:
    torchaudio.set_audio_backend("soundfile")
except Exception:
    pass

# ================= ⚙️ 頁面與全域設定 =================
st.set_page_config(
    page_title="族語影音降噪神器",
    page_icon="🎙️",
    layout="wide"
)

# --- 注入客製化 CSS 進行視覺強化 ---
st.markdown("""
<style>
    /* 整體字體微調放大 */
    html, body, [class*="css"] {
        font-size: 1.1rem;
    }
    
    /* 標題視覺強化 */
    h1 {
        font-size: 2.8rem !important;
        font-weight: 800 !important;
        color: #1E3A8A;
        margin-bottom: 0.5rem !important;
    }
    h2 {
        font-size: 2rem !important;
        font-weight: 700 !important;
        color: #2563EB;
    }
    h3 {
        font-size: 1.5rem !important;
        font-weight: 600 !important;
    }

    /* 將參數標籤字體加大至 20pt */
    [data-testid="stWidgetLabel"] p {
        font-size: 20pt !important;
        font-weight: 700 !important;
        color: #1E3A8A !important;
    }

    /* 上傳區塊強制中文化 */
    [data-testid="stFileUploadDropzone"] div[data-testid="stMarkdownContainer"] p {
        visibility: hidden;
        position: relative;
    }
    [data-testid="stFileUploadDropzone"] div[data-testid="stMarkdownContainer"] p::after {
        content: "請將檔案拖曳至此處";
        visibility: visible;
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        text-align: center;
        font-size: 1.3rem !important;
        font-weight: 600 !important;
        color: #4B5563 !important;
        display: block;
    }
    
    [data-testid="stFileUploadDropzone"] > div > div > span {
        visibility: hidden;
        position: relative;
    }
    [data-testid="stFileUploadDropzone"] > div > div > span::after {
        content: "請將檔案拖曳至此處";
        visibility: visible;
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        text-align: center;
        font-size: 1.3rem !important;
        font-weight: 600 !important;
        color: #4B5563 !important;
        display: block;
    }

    [data-testid="stFileUploader"] button {
        color: transparent !important;
        position: relative;
    }
    [data-testid="stFileUploader"] button::after {
        content: "瀏覽檔案";
        visibility: visible;
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        color: #1F2937 !important;
        font-size: 1.15rem !important;
        font-weight: 600 !important;
        white-space: nowrap;
    }

    /* 按鈕視覺強化 */
    .stButton > button {
        font-size: 1.15rem !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
        padding: 0.6rem 1.2rem !important;
        transition: all 0.3s ease;
        border: 1px solid #D1D5DB;
    }
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        border-color: #3B82F6;
    }
    
    .stAlert {
        font-weight: 500;
        font-size: 1.1rem;
    }
    
    /* 頁籤字體放大 */
    button[data-baseweb="tab"] p {
        font-size: 1.3rem !important;
        font-weight: 600 !important;
    }
</style>
""", unsafe_allow_html=True)

# ================= 📊 系統日誌與統計 =================
LOG_FILE = "denoise_usage_log.txt"

# 安全升級：優先從 Streamlit Secrets 讀取密碼，避免明文外流至 GitHub
if "ADMIN_PASSWORD" in st.secrets:
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
else:
    ADMIN_PASSWORD = "ilrdf"  # 若未設定 Secrets 的備用密碼

def log_usage(target_name, is_youtube):
    """將使用紀錄寫入本地 txt 檔案"""
    try:
        source_type = "YouTube" if is_youtube else "本機檔案"
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] 來源: {source_type} | 處理對象: {target_name}\n")
    except Exception:
        pass

def get_usage_data():
    """讀取總處理資料"""
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
                return lines
        return []
    except Exception:
        return []

# ================= 🔄 初始化 Session State =================
if "processed_file_path" not in st.session_state:
    st.session_state.processed_file_path = None
if "processed_file_name" not in st.session_state:
    st.session_state.processed_file_name = None
if "is_processing" not in st.session_state:
    st.session_state.is_processing = False
if "error_message" not in st.session_state:
    st.session_state.error_message = None
if "process_target" not in st.session_state:
    st.session_state.process_target = None
if "is_yt_source" not in st.session_state:
    st.session_state.is_yt_source = False

# ================= 🩹 系統補丁 =================
def apply_patches():
    try:
        import df.utils
        df.utils.get_git_root = lambda: "."
        df.utils.get_commit_hash = lambda: "web_v1"
        df.utils.get_branch_name = lambda: "master"
    except ImportError:
        pass

# ================= 🧠 AI 模型快取區 =================
@st.cache_resource(show_spinner="正在將 AI 模型載入伺服器記憶體 (僅需一次)...")
def load_ai_model():
    try:
        apply_patches()
        from df.enhance import init_df
        model, df_state, _ = init_df(model_base_dir=None)
        return model, df_state
    except ImportError as e:
        # 解除錯誤遮蔽：印出真正的 ImportError 原因
        raise RuntimeError(f"套件載入失敗！雲端真實錯誤訊息: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"模型初始化發生錯誤: {str(e)}")

# ================= 🌐 YouTube 下載功能 (深度反阻擋升級版) =================
def download_youtube_video(url, output_dir):
    # 1. 強制確保 yt-dlp 是全世界最新版 (因為 YouTube 每天都在更新防堵機制)
    try:
        subprocess.run(["pip", "install", "-U", "yt-dlp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("請執行: pip install yt-dlp")
        
    ydl_opts = {
        'format': 'best',
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'noplaylist': True, 
        'quiet': True, 
        'no_warnings': True,
        # ⬇️ 終極反阻擋策略：強制使用 IPv4，避免雲端 IPv6 被 YouTube 封鎖
        'source_address': '0.0.0.0',
        # ⬇️ 拔除 web 端，偽裝成 TV 或 Android 裝置 (限制最少)
        'extractor_args': {
            'youtube': {
                'client': ['tv', 'android', 'ios']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': '*/*',
        }
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 強制清除快取，避免舊的 HTTP 403 阻擋紀錄殘留
            ydl.cache.remove()
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except Exception as e:
        error_str = str(e)
        # 針對 403 錯誤提供專屬且易懂的提示
        if "403" in error_str or "Forbidden" in error_str:
            raise RuntimeError("YouTube 拒絕了雲端伺服器的下載請求 (HTTP 403)。這是因為 Streamlit 雲端 IP 被 YouTube 官方列入機器人黑名單。建議您先將影片下載至本機，再透過「本機檔案上傳」進行降噪。")
        else:
            raise RuntimeError(error_str)

# ================= 🛠️ 核心處理邏輯 =================
def process_media(source, atten_lim_db, is_youtube=False):
    """處理影音檔案的核心函式"""
    
    # 決定原始檔名
    if is_youtube:
        original_name = os.path.basename(source)
    else:
        original_name = source.name
        
    name, ext = os.path.splitext(original_name)
    audio_extensions = (".wav", ".mp3", ".m4a", ".aac", ".flac")
    is_audio_only = ext.lower() in audio_extensions
    output_ext = ext if is_audio_only else ".mp4"
    
    # 動態產生包含降噪強度的檔名
    final_output_name = f"{name}_{atten_lim_db}db{output_ext}"

    # 建立獨立暫存資料夾
    work_dir = tempfile.mkdtemp(prefix="denoise_")
    input_path = os.path.join(work_dir, original_name)
    output_path = os.path.join(work_dir, final_output_name)
    temp_noisy = os.path.join(work_dir, "temp_noisy.wav")
    temp_clean = os.path.join(work_dir, "temp_clean.wav")

    try:
        # 1. 準備來源檔案
        if is_youtube:
            shutil.copy(source, input_path)
        else:
            with open(input_path, "wb") as f:
                f.write(source.getbuffer())

        # 2. 提取音訊 (轉為 48kHz 單聲道 WAV)
        cmd_extract = [
            "ffmpeg", "-y", "-i", input_path, "-vn", "-acodec", "pcm_s16le", 
            "-ar", "48000", "-ac", "1", temp_noisy, "-hide_banner", "-loglevel", "error"
        ]
        subprocess.run(cmd_extract, check=True, capture_output=True)

        # 3. AI 降噪運算 (分段處理)
        model, df_state = load_ai_model()
        from df.enhance import load_audio, save_audio, enhance
        
        audio, _ = load_audio(temp_noisy, sr=df_state.sr())
        total_samples = audio.shape[-1]
        
        chunk_size = df_state.sr() * 10 # 每次處理 10 秒
        num_chunks = (total_samples + chunk_size - 1) // chunk_size
        
        progress_bar = st.progress(0)
        time_text = st.empty()
        
        enhanced_chunks = []
        start_time = time.time()

        for i in range(num_chunks):
            start_idx = i * chunk_size
            end_idx = min(start_idx + chunk_size, total_samples)
            
            # 擷取音訊並降噪
            audio_chunk = audio[:, start_idx:end_idx]
            clean_chunk = enhance(model, df_state, audio_chunk, atten_lim_db=atten_lim_db)
            enhanced_chunks.append(clean_chunk)
            
            # 更新進度與預估時間
            current_progress = (i + 1) / num_chunks
            progress_bar.progress(current_progress)
            
            elapsed = time.time() - start_time
            avg_time = elapsed / (i + 1)
            remaining_time = int(avg_time * (num_chunks - (i + 1)))
            time_text.markdown(f"**🤖 AI 運算中:** `已完成 {int(current_progress*100)}%` | `剩餘約 {remaining_time} 秒` (強度: {atten_lim_db}dB)")

        # 合併降噪後的片段
        enhanced_audio = torch.cat(enhanced_chunks, dim=-1)
        save_audio(temp_clean, enhanced_audio, df_state.sr())

        # 4. 合成最終影音檔案
        if is_audio_only:
            cmd_merge = [
                "ffmpeg", "-y", "-i", temp_clean, "-c:a", "libmp3lame", 
                "-q:a", "2", output_path, "-hide_banner", "-loglevel", "error"
            ]
        else:
            cmd_merge = [
                "ffmpeg", "-y", "-i", input_path, "-i", temp_clean, "-c:v", "copy", 
                "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", "-shortest", 
                output_path, "-hide_banner", "-loglevel", "error"
            ]
            
        subprocess.run(cmd_merge, check=True, capture_output=True)
        
        # 儲存結果路徑至 session_state
        st.session_state.processed_file_path = output_path
        st.session_state.processed_file_name = final_output_name
        
        # 成功後寫入 Log 紀錄
        log_usage(original_name, is_youtube)
        
        return True, "處理成功！"

    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode("utf-8", errors="ignore") if e.stderr else "無詳細錯誤"
        return False, f"FFmpeg 錯誤: {err_msg}"
    except Exception as e:
        return False, f"發生錯誤: {str(e)}"

# ================= 🖥️ 網頁前端介面 =================
def main():
    st.title("🎙️ 族語影音降噪神器")
    
    # ---------------- 側邊欄設定 ----------------
    with st.sidebar:
        st.header("⚙️ 參數設定")
        atten_lim = st.slider("降噪強度 (dB)", min_value=20, max_value=100, value=50, step=5)
        st.info("💡 建議：若噪音很雜設 100；想保留環境感設 40-60。")
        
        st.markdown("---")
        
        # 清除暫存按鈕
        if st.button("🗑️ 清除所有暫存紀錄", use_container_width=True):
            if st.session_state.processed_file_path:
                try: 
                    shutil.rmtree(os.path.dirname(st.session_state.processed_file_path))
                except Exception: 
                    pass
            st.session_state.processed_file_path = None
            st.session_state.processed_file_name = None
            st.session_state.is_processing = False
            st.session_state.process_target = None
            st.session_state.error_message = None
            st.rerun()
            
        # 管理員日誌區域
        st.markdown("---")
        st.subheader("🔑 管理員模式")
        admin_pwd = st.text_input("輸入管理密碼", type="password")
        
        usage_data = get_usage_data()
        st.caption(f"📊 累計處理人次: **{len(usage_data)}** 次")
        
        if admin_pwd == ADMIN_PASSWORD:
            st.success("密碼正確")
            if usage_data:
                # 下載 Log 按鈕
                log_content = "".join(usage_data)
                st.download_button(
                    label="⬇️ 下載完整使用日誌",
                    data=log_content,
                    file_name=f"denoise_log_{datetime.date.today()}.txt",
                    mime="text/plain",
                    use_container_width=True
                )
                # 預覽最近 5 筆紀錄
                st.markdown("**最近使用紀錄:**")
                for line in usage_data[-5:]:
                    st.caption(line.strip())
            else:
                st.write("目前尚無日誌紀錄。")

    # ---------------- 主畫面佈局 ----------------
    col1, col2 = st.columns([1, 1])
    
    # 左側欄位：上傳與輸入區
    with col1:
        st.subheader("📥 選擇來源")
        tab_local, tab_yt = st.tabs(["📁 本機檔案上傳", "🌐 YouTube 網址"])
        
        # 頁籤 1：本機上傳
        with tab_local:
            supported = ("mp4", "mov", "avi", "mkv", "wav", "mp3", "m4a", "aac", "flac")
            uploaded_file = st.file_uploader("請選擇要降噪的檔案", type=supported)
            
            if uploaded_file and not st.session_state.processed_file_path:
                if st.button("🚀 開始降噪處理 (本機)", use_container_width=True):
                    st.session_state.process_target = uploaded_file
                    st.session_state.is_yt_source = False
                    st.session_state.is_processing = True
                    st.rerun()

        # 頁籤 2：YouTube 網址
        with tab_yt:
            yt_url = st.text_input("請輸入 YouTube 影片網址", placeholder="https://www.youtube.com/watch?v=...")
            
            if yt_url and not st.session_state.processed_file_path:
                if st.button("🚀 開始降噪處理 (YouTube)", use_container_width=True):
                    st.session_state.process_target = yt_url
                    st.session_state.is_yt_source = True
                    st.session_state.is_processing = True
                    st.rerun()

        # 處理進度顯示區塊
        if st.session_state.is_processing:
            with st.status("AI 降噪處理中...", expanded=True) as status:
                success = False
                msg = ""
                
                if st.session_state.is_yt_source:
                    st.write("🌐 正在從 YouTube 下載影音... (依網路速度而定，請稍候)")
                    try:
                        temp_yt_dir = tempfile.mkdtemp(prefix="yt_")
                        downloaded_path = download_youtube_video(st.session_state.process_target, temp_yt_dir)
                        
                        st.write("⏳ 步驟 1/3: YouTube 下載完成，正在提取並轉換音訊格式...")
                        success, msg = process_media(downloaded_path, atten_lim, is_youtube=True)
                    except Exception as e: 
                        success = False
                        msg = f"{str(e)}"
                else:
                    st.write("⏳ 步驟 1/3: 正在提取並轉換音訊格式...")
                    success, msg = process_media(st.session_state.process_target, atten_lim, is_youtube=False)
                
                # 處理完畢更新狀態
                st.session_state.is_processing = False
                
                if success: 
                    status.update(label="✅ 處理完成！", state="complete")
                    st.rerun()
                else: 
                    status.update(label="❌ 處理失敗", state="error")
                    st.session_state.error_message = msg
                    st.rerun()

        # 錯誤訊息顯示區
        if st.session_state.error_message:
            st.error(st.session_state.error_message)
            if st.button("🔄 重試"): 
                st.session_state.error_message = None
                st.rerun()

    # 右側欄位：預覽與下載區
    with col2:
        st.subheader("🎬 成果預覽與下載")
        
        if st.session_state.processed_file_path and os.path.exists(st.session_state.processed_file_path):
            file_ext = os.path.splitext(st.session_state.processed_file_name)[1].lower()
            
            # 讀取檔案進行預覽
            with open(st.session_state.processed_file_path, "rb") as f:
                bytes_data = f.read()
                
            if file_ext in (".mp4", ".mov", ".avi", ".mkv"): 
                st.video(bytes_data)
            else: 
                st.audio(bytes_data)
                
            # 下載按鈕
            st.download_button(
                label=f"⬇️ 下載成果 ({st.session_state.processed_file_name})", 
                data=bytes_data, 
                file_name=st.session_state.processed_file_name, 
                use_container_width=True
            )
            
            # 處理下一個檔案的按鈕 (包含清理暫存邏輯)
            if st.button("🔄 處理下一個檔案", use_container_width=True):
                try: 
                    shutil.rmtree(os.path.dirname(st.session_state.processed_file_path))
                except Exception: 
                    pass
                st.session_state.processed_file_path = None
                st.session_state.processed_file_name = None
                st.session_state.error_message = None
                st.session_state.process_target = None
                st.rerun()
        else: 
            st.write("目前尚無處理好的檔案。")

if __name__ == "__main__":
    main()
