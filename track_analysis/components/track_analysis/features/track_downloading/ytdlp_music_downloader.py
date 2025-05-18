import os.path
import re
import time
from pathlib import Path
from typing import List, Dict, Optional

import yt_dlp
from yt_dlp.postprocessor import PostProcessor

from track_analysis.components.md_common_python.py_common.logging import HoornLogger
from track_analysis.components.track_analysis.constants import COOKIES_FILE, OUTPUT_DIRECTORY, FFMPEG_PATH, \
    DOWNLOAD_CSV_FILE
from track_analysis.components.track_analysis.features.track_downloading.model.download_model import DownloadModel
from track_analysis.components.track_analysis.features.track_downloading.music_download_interface import \
    MusicDownloadInterface


class PostProcessorHelper(PostProcessor):
    def __init__(self, logger: HoornLogger):
        super().__init__()
        self._logger = logger
        self._latest_path = ""

    def run(self, information):
        # Do something
        self._latest_path = information["filepath"]
        self._logger.info(f"Extracted path: '{self._latest_path}'")
        return [], information

    def get_last_path(self) -> str:
        self._logger.debug(f"Returning path: '{self._latest_path}'")
        return self._latest_path


class YTDLPMusicDownloader(MusicDownloadInterface):
    def __init__(self, logger: HoornLogger):
        super().__init__(is_child=True)
        self._logger = logger
        self._logger.debug("YTDLPMusicDownloader initialized")

    def download_tracks(self) -> Optional[List[DownloadModel]]:
        choice = self._get_choice()
        if choice.lower() =='single':
            return [self._download_single_track()]
        elif choice.lower() == 'csv':
            return self._download_csv_tracks()

    def _download_single_track(self) -> DownloadModel:
        url = input("Enter the music URL: ")
        recording_id: str = input("Enter the recording ID: ")
        album_id: str = input("Enter the album ID: ")
        path: Path = self._download_urls([url])[url]
        return DownloadModel(url=url, path=path, recording_id=recording_id, release_id=album_id)

    def _download_csv_tracks(self) -> List[DownloadModel]:
        file_path = input("Enter the file path containing the music URLs (csv, leave empty for default): ")
        want_to_detect_genres_automatically = input("Do you want to detect genres automatically? (y/n): ").lower() == 'y'

        if file_path == "":
            file_path = DOWNLOAD_CSV_FILE
        else: file_path = Path(file_path)

        # validate file path
        if not os.path.isfile(file_path):
            self._logger.error(f"File not found: {file_path}")
            return []

        with open(file_path, 'r', encoding='utf-8') as file:
            data = file.readlines()[1:] # Skip the header line
            url_data = {}

            for line in data:
                parts = line.strip().split(',')
                url_data[parts[0]] = {
                    "release id": parts[1],
                    "recording id": parts[2],
                    "genre": parts[4] if not want_to_detect_genres_automatically else None,
                    "subgenres": parts[5] if not want_to_detect_genres_automatically else None
                }

            paths: Dict[str, Path] = self._download_urls([url for url, _ in url_data.items()])

            download_models: List[DownloadModel] = []
            for url, path in paths.items():
                download_models.append(DownloadModel(
                    url=url,
                    path=path,
                    release_id=url_data[url]['release id'],
                    recording_id=url_data[url]['recording id'],
                    genre=url_data[url]['genre'],
                    subgenre=url_data[url]['subgenres']
                ))

            missed_urls = [url for url in url_data.keys() if url not in paths.keys()]
            for url in missed_urls:
                self._logger.warning(f"The following url could not be downloaded: {url}, id: {url_data[url]['recording id']}")

            return download_models

    def _download_urls(self, urls: List[str]) -> Dict[str, Path]:
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                # 'preferredcodec': 'flac',
                # 'preferredquality': '192',
            }],
            'outtmpl': os.path.join(OUTPUT_DIRECTORY, '%(title)s.%(ext)s'),
            'noplaylist': True,
            "cookiefile": str(COOKIES_FILE),
            "ffmpeg_location": str(FFMPEG_PATH)
        }

        downloaded_files: Dict[str, Path] = {}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            post_processor_helper = PostProcessorHelper(self._logger)
            ydl.add_post_processor(post_processor_helper)

            for url in urls:
                retries = 3
                backoff_factor = 2
                for i in range(retries):
                    try:
                        # Step 1: Pre-fetch to clean the title
                        info_dict = ydl.extract_info(url, download=False)
                        title = info_dict.get('title', 'audio')
                        title = self._clean_filename(title)

                        # Step 2: Set custom output filename template
                        ydl_opts['outtmpl']['default'] = os.path.join(OUTPUT_DIRECTORY, f'{title}.%(ext)s')

                        ydl.download([url])
                        downloaded_files[url] = Path(post_processor_helper.get_last_path())

                        time.sleep(0.5)
                        break  # Comment this line if you want to torment your soul for all eternity.
                    except yt_dlp.utils.DownloadError as e:
                        wait_time = backoff_factor ** i
                        self._logger.warning(f"Error downloading '{url}': {e}, retrying in {wait_time} seconds...")
                        time.sleep(wait_time)
                    except Exception as e:
                        self._logger.error(f"An error occurred while downloading '{url}': {e}")
                        break

        return downloaded_files

    def _get_choice(self) -> str:
        choice = input("Choose a download option (single/csv): ")
        if choice.lower() not in ['single', 'csv']:
            self._logger.error(f"Invalid option '{choice}'. Choose from: single, multiple")
            return self._get_choice()

        return choice

    def _clean_filename(self, filename: str, replacement_char: str = '_') -> str:
        """
        Cleans a filename by:
        - Replacing unsupported or non-UTF-8-safe characters
        - Replacing invalid filename characters
        - Removing leading/trailing spaces or dots

        Args:
            filename: The filename to clean.
            replacement_char: The character to replace unsupported characters with.

        Returns:
            The cleaned filename.
        """
        # Replace characters not encodable in UTF-8
        safe_chars = []
        for char in filename:
            try:
                char.encode('utf-8')
                safe_chars.append(char)
            except UnicodeEncodeError:
                safe_chars.append(replacement_char)
        filename = ''.join(safe_chars)

        # Define a regular expression for invalid filename characters
        invalid_chars = r'[<>:"/\\|?*\x00-\x1f]'

        # Replace them with the replacement character
        filename = re.sub(invalid_chars, replacement_char, filename)

        # Replace non-printable characters
        filename = ''.join(
            c if c.isprintable() else replacement_char
            for c in filename
        )

        # Remove leading and trailing dots and spaces
        filename = filename.strip(' .')

        # Ensure it's not empty
        if not filename:
            filename = replacement_char

        return filename
