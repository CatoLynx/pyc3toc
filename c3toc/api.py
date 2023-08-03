"""
Copyright (C) 2023 Julian Metzler

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import dateutil.parser
import datetime
import requests

from .exceptions import APIError


class C3TOCAPI:
    def __init__(self, host="api.c3toc.de"):
        self.host = host
        self.train_info = {}
    
    def get_trains(self, format="json"):
        if format not in ("json", "geojson"):
            raise ValueError("Unknown data format: {format}".format(format=format))
        response = requests.get("https://{host}/trains.{format}".format(host=self.host, format=format))
        if response.status_code != 200:
            raise APIError("Server returned HTTP status {code}".format(code=response.status_code))
        data = response.json()
        return data
    
    def get_tracks(self, format="json"):
        if format not in ("json", "geojson"):
            raise ValueError("Unknown data format: {format}".format(format=format))
        response = requests.get("https://{host}/tracks.{format}".format(host=self.host, format=format))
        if response.status_code != 200:
            raise APIError("Server returned HTTP status {code}".format(code=response.status_code))
        data = response.json()
        return data
    
    def _calc_avg_speed(self, history, minutes, track_length):
        """
        Calculates the average speed in trackmarker units per second
        over the last <minutes> minutes.
        Returns the average speed , time range and the history list
        with any entries older than specified removed.
        The calculation assumes that the train does not make
        more than one round within the lookback period.
        """
        new_history = []
        now = datetime.datetime.utcnow()
        for timestamp, trackmarker in history:
            delta = now - timestamp
            seconds = delta.total_seconds()
            if seconds > minutes * 60 or seconds < 0:
                # Also filter out timestamps in the future, just to be sure
                continue
            new_history.append((timestamp, trackmarker))
        if len(new_history) < 2:
            # Can't calculate an average yet
            return None, None, None
        trackmarker_delta = (new_history[-1][1] - new_history[0][1])
        if trackmarker_delta < 0:
            trackmarker_delta += track_length
        seconds_delta = (new_history[-1][0] - new_history[0][0]).total_seconds()
        avg_speed = trackmarker_delta / seconds_delta
        return avg_speed, seconds_delta, new_history
    
    def get_train_info(self, display_trackmarker, eta_lookback, eta_max_jump, trackmarker_delta_arrived, track_length):
        # display_trackmarker: Physical trackmarker position of the display
        # eta_lookback: How many minutes of past train positions to consider for ETA
        # eta_max_jump: Maximum ETA jump in seconds
        # trackmarker_delta_arrived: "station zone" size in track units
        # track_length: Length of the track in track units
        
        utcnow = datetime.datetime.utcnow()
        
        # Get trains from API
        trains = self.get_trains()['trains'].items()
        for pos, train in enumerate(trains):
            name, data = train
            # Parse "last update" timestamp"
            timestamp = dateutil.parser.isoparse(data['timestamp'] + "Z").replace(tzinfo=None)
            
            # Init train data if train is new
            if name not in self.train_info:
                self.train_info[name] = {
                    'history': [],
                    'avg_speed': 0.0,
                    'raw_eta': None,
                    'eta': None,
                    'arrived': False
                }
            
            # Add current location to history if timestamp not yet present
            if timestamp not in [e[0] for e in self.train_info[name]['history']]:
                self.train_info[name]['history'].append((timestamp, data['trackmarker']))
            
            # Calculate average speed
            avg_speed, seconds_delta, history = self._calc_avg_speed(self.train_info[name]['history'], eta_lookback, track_length) # 5 minutes
            
            # Update average speed
            self.train_info[name]['avg_speed'] = avg_speed
            
            # Update history with truncated version returned by _calc_avg_speed
            if history is not None:
                self.train_info[name]['history'] = history
            
            # Calculate distance between display and train in track units
            trackmarker_delta = display_trackmarker - data['trackmarker']
            if trackmarker_delta < 0:
                trackmarker_delta += track_length
            
            # If train is within a certain distance, mark as arrived
            allow_eta_jump = False
            if trackmarker_delta < trackmarker_delta_arrived:
                self.train_info[name]['arrived'] = True
            else:
                if self.train_info[name]['arrived']:
                    # Train was marked as arrived, but isn't anymore.
                    # This most likely means it has left the station.
                    # This means we must allow the ETA to jump up.
                    allow_eta_jump = True
                self.train_info[name]['arrived'] = False
            
            # Set ETA to now if train is marked as arrived
            if self.train_info[name]['arrived']:
                self.train_info[name]['eta'] = self.train_info[name]['raw_eta'] = utcnow
            else:
                # Skip ETA calculation if average speed is 0 or history spans less than 2 minutes
                if self.train_info[name]['avg_speed'] == 0 or seconds_delta is None or seconds_delta < 2 * 60:
                    self.train_info[name]['raw_eta'] = None
                else:
                    # The raw ETA is just the last history timestamp plus the linearly extrapolated time
                    self.train_info[name]['raw_eta'] = self.train_info[name]['history'][-1][0] + datetime.timedelta(seconds=(trackmarker_delta / self.train_info[name]['avg_speed']))
                
                # Calculate soft ETA based on raw ETA. It is only allowed to vary by so much in one cycle
                if self.train_info[name]['raw_eta'] is not None:
                    if allow_eta_jump:
                        # If we allowed an ETA jump, ignore limitations and reset flag
                        self.train_info[name]['eta'] = self.train_info[name]['raw_eta']
                        allow_eta_jump = False
                    else:
                        eta = self.train_info[name]['eta'] or self.train_info[name]['raw_eta']
                        delta = (self.train_info[name]['raw_eta'] - eta).total_seconds()
                        
                        if (delta > eta_max_jump):
                            eta += datetime.timedelta(seconds=eta_max_jump)
                        elif (delta < -eta_max_jump):
                            eta -= datetime.timedelta(seconds=eta_max_jump)
                        else:
                            eta = self.train_info[name]['raw_eta']
                    
                        self.train_info[name]['eta'] = eta
                else:
                    self.train_info[name]['eta'] = None
        return self.train_info
            