import requests
import gzip
import tempfile
import logging
from pathlib import Path
from xmlrpc.client import Error
from ffprobe import FFProbe
from pythonopensubtitles.opensubtitles import OpenSubtitles
from pythonopensubtitles.utils import File
from pymkv import MKVFile, MKVTrack
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler
from time import sleep

logging.basicConfig(
    format="%(asctime)s - %(message)s",
    datefmt="%m/%d/%Y %I:%M:%S %p",
    level=logging.INFO,
)


class AutoSub:

    """
    Watches folder path for newly added mkv files and, for each:\n
        - gets ffprobe stats\n
        - converts dts to specified codec as new audio stream\n
        - downloads subtitles for specified language from opensubtitles.org\n
        - adds new subtitle track\n
        - sets subtitle language and sets it as default\n

    Attributes
    ----------
    os_username : str
        opensubtitles.org username
    os_password : str
        opensubtitles.org password
    os_language : str
        subtitle language
    watch_path : str
        filesystem path to watch for created MKV files. Defaults to "."
    watch_recursive : bool
        wether to watch subfolders of the `watch_path` recursively or not. Defaults `True`

    Methods
    -------
    watch():
        Starts the AutoSub folder watcher
    download_subtitle():
        Downloads subtitles from opensubtitles.org, in the defined language (`os_language`) and stores them in a tempfile
    add_subtitle(set_default=True):
        Muxes the subtitle in the MKV file and sets it as default if the flag is set appropriately (Default `True`)
    """

    def __init__(
        self,
        os_useraname: str,
        os_password: str,
        os_language: str,
        watch_path: str = ".",
        watch_recursive: bool = True,
    ):
        self.os_username = os_useraname
        self.os_password = os_password
        self.os_language = os_language
        self.watch_path = Path(watch_path)
        self.watch_recursive = watch_recursive

        self._logger = logging.getLogger("AutoSub")

        self._event_handler = PatternMatchingEventHandler(
            patterns=["*.mkv"],
            ignore_patterns=[],
            ignore_directories=True,
            case_sensitive=False,
        )
        self._event_handler.on_any_event = self._on_any_event
        self._observer = Observer()
        self._observer.schedule(
            self._event_handler,
            self.watch_path,
            recursive=self.watch_recursive,
        )

    def __repr__(self):
        return f"AutoSub listening on {self.watch_path.absolute()}"

    def _get_stats(self):
        # TODO refactor to consume path on event, potentially from queue
        stats = list()
        for path in self.watch_path.glob("*.mkv"):
            metadata = FFProbe(path.name)
            video_stream_id = 0
            audio_tracks = list()
            has_dts = False
            dts_tracks = 0
            sub_tracks = 0
            has_lang = False
            for idx, stream in enumerate(metadata.streams):
                if stream.is_video():
                    video_stream_id = idx
                if stream.is_audio():
                    audio_tracks.append(
                        {
                            "codec": stream.codec(),
                            "stream_id": idx,
                            "stream_name": stream.__dict__.get("TAG:title", None),
                        }
                    )
                    if stream.codec() == "dts":
                        dts_tracks += 1
                        has_dts = True
                if stream.is_subtitle():
                    sub_tracks += 1
                    if stream.language() == "rum":
                        has_lang = True

            fstat = {
                "file_path": path,
                "video_stream_id": video_stream_id,
                "audio_tracks": audio_tracks,
                "has_dts": has_dts,
                "dts_tracks": dts_tracks,
                "sub_tracks": sub_tracks,
                "has_lang": has_lang,
            }
            stats.append(fstat)

        return stats

    def _on_any_event(self, event):
        # TODO replace with download and add sub methods. Consider adding threading and queue.
        self._logger.info(event)

    def _stop(self):
        self._observer.stop()
        self._observer.join()

    def watch(self):
        self._observer.start()
        try:
            self._logger.info(f"Starting watcher on {self.watch_path.absolute()}")
            while True:
                sleep(0.2)
        except KeyboardInterrupt:
            self._logger.info("Stopping AutoSub")
            self._stop()

    def download_subtitle(self):
        """
        Downloads subtitles from opensubtitles.org, in the defined language and stores them in a tempfile.
        Search is trying to match movie by hash and if it is unsuccessful, it searches by movie name.
        Only first match is considered.
        """
        ost = OpenSubtitles()
        logged_in = ost.login(self.os_username, self.os_password)
        if not logged_in:
            raise Error("Wrong opensubtitles credentials")
        # TODO refactor to consume path on event, potentially from queue
        mkv_files = [mkv for mkv in self.watch_path.glob("*.mkv")]
        subs = list()
        for movie in mkv_files:
            movie_file = File(movie.absolute())
            # search by hash, if not, by name
            ost_data = ost.search_subtitles(
                [
                    {
                        "sublanguageid": self.os_language
                        if len(self.os_language) == 3
                        else self.os_language,
                        "moviehash": movie_file.get_hash(),
                    },
                    {
                        "sublanguageid": self.os_language
                        if len(self.os_language) == 3
                        else self.os_language,
                        "query": movie.name,
                    },
                ]
            )
            if ost_data:
                # #  downloading first subtitle
                plain_link = ost_data[0]["SubDownloadLink"]
                sub_link_parts = plain_link.split("/download/")
                #  rebuilding link to get utf-8 subtitle
                sub_link = (
                    sub_link_parts[0]
                    + "/download/subencoding-utf8/"
                    + sub_link_parts[1]
                )
                response = requests.get(sub_link)
                tmp, tmp_name = tempfile.mkstemp()
                with open(tmp, "wb") as srt_out:
                    srt_out.write(gzip.decompress(response.content))
                subs.append({"file_path": movie, "sub": tmp_name})
            else:
                subs.append({"file_path": movie, "sub": None})
        return subs

    def add_subtitle(self, set_default=True):
        """
        Muxes the subtitle in the MKV file and sets it as default if the flag is set appropriately (default True)

        Parameters
        ----------
            set_default : bool
                wether to set the muxed subtitle as default subtitle track. Default is `True`
        """
        stats = self._get_stats()
        subs = self.download_subtitles()
        movies = list()
        for idx, _ in enumerate(stats):
            movies.append({**stats[idx], **subs[idx]})

        for movie in movies:
            if not movie["has_lang"]:
                sub_track = MKVTrack(movie["sub"])
                sub_track.language = self.os_language
                sub_track.default_track = True if set_default else False
                mkv_file = MKVFile(movie["file_path"].absolute())
                mkv_file.add_track(sub_track)
                # TODO Handle output folder or file replace config
                mkv_file.mux(movie["file_path"].stem + "_w_sub.mkv")

        return True


# [{'file_path': PosixPath('Up in the Air 2009 1080p BluRay DTS x264-CtrlHD.mkv'), 'audio_tracks': 2, 'has_dts': True, 'dts_tracks': 1, 'sub_tracks': 0, 'has_lang': False, 'sub': '/tmp/tmpyeq93w1e'}]