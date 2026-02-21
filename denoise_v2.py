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
import csv
import uuid

# 忽略警告
warnings.filterwarnings("ignore")

# 強制設定 Windows 音訊後端 (本機開發用，雲端 Linux 通常內建)
try:
    torchaudio.set_audio_backend("soundfile")
except Exception:
    pass

# ================= ⚙️ 頁面與全域設定 =================
st.set_page_config(
    page_title="Suyang! 族語影音降噪工具",
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
</style>
""", unsafe_allow_html=True)

# ================= 🔄 初始化 Session State =================
if "session_id" not in st.session_state:
    # 產生一組 4 碼的隨機代碼作為訪客 ID
    st.session_state.session_id = uuid.uuid4().hex[:4].upper()
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

# ================= 📊 系統日誌與統計 (升級 CSV 版) =================
LOG_FILE = "denoise_usage_log.csv"

# 安全升級：優先從 Streamlit Secrets 讀取密碼
if "ADMIN_PASSWORD" in st.secrets:
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
else:
    ADMIN_PASSWORD = "ilrdf"

def log_usage(user_name, original_name, file_size_mb, atten_lim_db, duration_sec, status, error_info):
    """將使用紀錄完整寫入本地 CSV 檔案"""
    try:
        # 強制設定為台灣台北時間 (UTC+8)
        tz_taipei = datetime.timezone(datetime.timedelta(hours=8))
        timestamp = datetime.datetime.now(tz_taipei).strftime("%Y-%m-%d %H:%M:%S")
        
        # 判斷檔案類型
        ext = os.path.splitext(original_name)[1].lower()
        file_type = "音檔" if ext in [".wav", ".mp3", ".m4a", ".aac", ".flac"] else "影片"
        
        file_exists = os.path.isfile(LOG_FILE)
        
        # 使用 utf-8-sig 確保 Excel 開啟時不會有中文亂碼
        with open(LOG_FILE, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            # 若檔案不存在，先寫入標題列
            if not file_exists:
                writer.writerow(["處理時間", "使用者姓名", "原始檔名", "檔案類型", "檔案大小(MB)", "降噪強度(dB)", "處理耗時(秒)", "處理狀態", "錯誤詳細資訊"])
            
            # 寫入本次數據
            writer.writerow([timestamp, user_name, original_name, file_type, file_size_mb, atten_lim_db, duration_sec, status, error_info])
    except Exception:
        pass

def get_usage_data():
    """讀取總處理資料"""
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8-sig") as f:
                lines = f.readlines()
                return lines
        return []
    except Exception:
        return []

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
        raise RuntimeError(f"套件載入失敗！雲端真實錯誤訊息: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"模型初始化發生錯誤: {str(e)}")

# ================= 🛠️ 核心處理邏輯 =================
def process_media(source, atten_lim_db, user_name):
    """處理影音檔案的核心函式，並包含完整的數據紀錄"""
    global_start_time = time.time()
    
    original_name = source.name
    # 計算檔案大小 (MB)，保留兩位小數
    file_size_mb = round(source.size / (1024 * 1024), 2)
        
    name, ext = os.path.splitext(original_name)
    audio_extensions = (".wav", ".mp3", ".m4a", ".aac", ".flac")
    is_audio_only = ext.lower() in audio_extensions
    output_ext = ext if is_audio_only else ".mp4"
    
    final_output_name = f"{name}_{atten_lim_db}db{output_ext}"

    work_dir = tempfile.mkdtemp(prefix="denoise_")
    input_path = os.path.join(work_dir, original_name)
    output_path = os.path.join(work_dir, final_output_name)
    temp_noisy = os.path.join(work_dir, "temp_noisy.wav")
    temp_clean = os.path.join(work_dir, "temp_clean.wav")

    try:
        # 1. 準備來源檔案
        with open(input_path, "wb") as f:
            f.write(source.getbuffer())

        # 2. 提取音訊
        cmd_extract = [
            "ffmpeg", "-y", "-i", input_path, "-vn", "-acodec", "pcm_s16le", 
            "-ar", "48000", "-ac", "1", temp_noisy, "-hide_banner", "-loglevel", "error"
        ]
        subprocess.run(cmd_extract, check=True, capture_output=True)

        # 3. AI 降噪運算
        model, df_state = load_ai_model()
        from df.enhance import load_audio, save_audio, enhance
        
        audio, _ = load_audio(temp_noisy, sr=df_state.sr())
        total_samples = audio.shape[-1]
        
        chunk_size = df_state.sr() * 10 
        num_chunks = (total_samples + chunk_size - 1) // chunk_size
        
        progress_bar = st.progress(0)
        time_text = st.empty()
        
        enhanced_chunks = []
        start_time = time.time()

        for i in range(num_chunks):
            start_idx = i * chunk_size
            end_idx = min(start_idx + chunk_size, total_samples)
            
            audio_chunk = audio[:, start_idx:end_idx]
            clean_chunk = enhance(model, df_state, audio_chunk, atten_lim_db=atten_lim_db)
            enhanced_chunks.append(clean_chunk)
            
            current_progress = (i + 1) / num_chunks
            progress_bar.progress(current_progress)
            
            elapsed = time.time() - start_time
            avg_time = elapsed / (i + 1)
            remaining_time = int(avg_time * (num_chunks - (i + 1)))
            time_text.markdown(f"**🤖 AI 運算中:** `已完成 {int(current_progress*100)}%` | `剩餘約 {remaining_time} 秒` (強度: {atten_lim_db}dB)")

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
        
        st.session_state.processed_file_path = output_path
        st.session_state.processed_file_name = final_output_name
        
        # 成功後寫入 CSV Log
        duration_sec = round(time.time() - global_start_time, 1)
        log_usage(user_name, original_name, file_size_mb, atten_lim_db, duration_sec, "成功", "無")
        
        return True, "處理成功！"

    except subprocess.CalledProcessError as e:
        duration_sec = round(time.time() - global_start_time, 1)
        err_msg = e.stderr.decode("utf-8", errors="ignore") if e.stderr else "無詳細錯誤"
        full_err = f"FFmpeg 錯誤: {err_msg}"
        log_usage(user_name, original_name, file_size_mb, atten_lim_db, duration_sec, "失敗", full_err)
        return False, full_err
    except Exception as e:
        duration_sec = round(time.time() - global_start_time, 1)
        full_err = f"發生錯誤: {str(e)}"
        log_usage(user_name, original_name, file_size_mb, atten_lim_db, duration_sec, "失敗", full_err)
        return False, full_err

# ================= 🖥️ 網頁前端介面 =================
def main():
    st.title("🎙️ Suyang! 族語影音降噪工具")
    
    # ---------------- 📖 操作指引區塊 (置於首頁大標題下) ----------------
    st.info("💡 **快速使用**： 1️⃣ 左方上傳檔案 :red[**➔**] 2️⃣ 點擊開始降噪 :red[**➔**] 3️⃣ 右方試聽與下載 (可於左側邊欄微調強度)")
    
    with st.expander("📖 查看詳細操作說明 (初次使用建議閱讀)", expanded=False):
        st.markdown("""
        #### 🛠️ 使用步驟
        1. **📥 上傳檔案**：將需要處理的影音檔案（支援 `.mp4`, `.wav`, `.mp3` 等）拖曳或點選上傳至左下方的「檔案上傳」區塊。
        2. **🎛️ 調整強度 (可選)**：展開最左側的隱藏邊欄 (點擊 〉符號)，您可以填寫姓名並調整「降噪強度」。
           - **預設 50dB**：適合多數日常錄音，能有效去噪並保留人聲自然度。
           - **最高 100dB**：適合背景非常吵雜（如強風、馬路邊、冷氣聲）的環境。
        3. **🚀 執行降噪**：按下「開始降噪處理」按鈕，系統會顯示目前進度與預估時間，請耐心等待。
        4. **💾 預覽與下載**：處理完畢後，右側畫面會出現播放器。您可以先試聽/試看，確認滿意後再點擊按鈕下載。

        ⚠️ **隱私與安全聲明**：本系統為自動化即時處理。當您下載檔案或點擊「處理下一個」時，伺服器會自動銷毀您的所有影音暫存檔，絕不留存原始資料，請安心使用！
        """)

    st.markdown("---") # 分隔線
    
    # ---------------- 側邊欄設定 ----------------
    with st.sidebar:
        # 新增：使用者身分區塊
        st.header("👤 使用者身分")
        user_name_input = st.text_input("您的姓名 / 單位 (選填)", help="留下姓名能幫助我們統計各單位的使用狀況喔！")
        
        # 判斷是否填寫，未填寫則給予包含 Session ID 的預設訪客名稱
        if not user_name_input.strip():
            current_user = f"訪客_{st.session_state.session_id}"
        else:
            current_user = user_name_input.strip()
            # 成功輸入後顯示專屬族語歡迎語
            st.success(f"Embiyax su hug? 歡迎您，{current_user}！")
            
        st.markdown("---")
        
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
        # 扣除掉標題列 (Header) 的數量
        total_count = len(usage_data) - 1 if len(usage_data) > 0 else 0
        st.caption(f"📊 累計處理人次: **{total_count}** 次")
        
        if admin_pwd == ADMIN_PASSWORD:
            st.success("密碼正確")
            if usage_data:
                log_content = "".join(usage_data)
                # 升級：下載按鈕轉換為 CSV 格式下載
                st.download_button(
                    label="⬇️ 下載完整使用數據 (CSV)",
                    data=log_content.encode("utf-8-sig"),
                    file_name=f"denoise_log_{datetime.date.today()}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
                
                # 預覽最近 3 筆紀錄 (因為 CSV 較長，所以只預覽 3 筆避免版面過滿)
                st.markdown("**最近使用紀錄 (CSV原始資料):**")
                for line in usage_data[-3:]:
                    if line.strip():
                        st.caption(line.strip())
            else:
                st.write("目前尚無日誌紀錄。")

    # ---------------- 主畫面佈局 ----------------
    col1, col2 = st.columns([1, 1])
    
    # 左側欄位：上傳與輸入區
    with col1:
        st.subheader("📥 檔案上傳")
        
        supported = ("mp4", "mov", "avi", "mkv", "wav", "mp3", "m4a", "aac", "flac")
        uploaded_file = st.file_uploader("請選擇要降噪的檔案", type=supported)
        
        if uploaded_file and not st.session_state.processed_file_path:
            if st.button("🚀 開始降噪處理", use_container_width=True):
                st.session_state.process_target = uploaded_file
                st.session_state.is_processing = True
                st.rerun()

        # 處理進度顯示區塊
        if st.session_state.is_processing:
            with st.status("AI 降噪處理中...", expanded=True) as status:
                st.write("⏳ 步驟 1/3: 正在提取並轉換音訊格式...")
                # 升級：把目前使用者名稱 current_user 傳給處理函式作紀錄
                success, msg = process_media(st.session_state.process_target, atten_lim, current_user)
                
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
                label=f"⬇️ 下載降噪後檔案 ({st.session_state.processed_file_name})", 
                data=bytes_data, 
                file_name=st.session_state.processed_file_name, 
                use_container_width=True
            )
            
            # 處理下一個檔案的按鈕 (包含清理暫存邏輯)
            if st.button("🔄 繼續處理下一個檔案", use_container_width=True):
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
