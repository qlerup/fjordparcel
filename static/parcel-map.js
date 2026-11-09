/* Leaflet, shared with Urban Explorer. Routes are cached server-side. */
(() => {
  const instances = new WeakMap();
  const text = value => {
    const node = document.createElement('span');
    node.textContent = value;
    return node;
  };

  function create(canvas, figure, snapshot = false) {
    const L = window.L;
    if (!L) throw new Error('Leaflet unavailable');
    canvas.hidden = false;
    const map = L.map(canvas, {scrollWheelZoom: true, maxZoom: 19, zoomAnimation: !snapshot, fadeAnimation: !snapshot});
    const streets = L.maplibreGL({
      style: 'https://tiles.openfreemap.org/styles/liberty',
      preserveDrawingBuffer: snapshot,
      attributionControl: {customAttribution: '<a href="https://openfreemap.org/">OpenFreeMap</a> &copy; <a href="https://www.openmaptiles.org/">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'},
    }).addTo(map);
    const satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxNativeZoom: 19,
      attribution: 'Tiles &copy; Esri, Maxar, Earthstar Geographics, GIS User Community',
    });
    L.control.layers({'Vejkort': streets, 'Satellit': satellite}, {}, {collapsed: true}).addTo(map);
    const observer = new ResizeObserver(() => {
      if (!canvas.isConnected) { observer.disconnect(); return; }
      if (canvas.clientWidth) map.invalidateSize({pan: false});
    });
    observer.observe(canvas);
    figure.querySelector('[data-map-expand]').addEventListener('click', event => {
      const expanded = figure.classList.toggle('is-expanded');
      event.currentTarget.setAttribute('aria-pressed', String(expanded));
      event.currentTarget.textContent = expanded ? 'Lille kort' : 'Stort kort';
      map.invalidateSize({pan: false});
    });
    return {map, basemap: streets, lines: L.layerGroup().addTo(map), markers: L.layerGroup().addTo(map)};
  }

  function draw(state, data, status) {
    const L = window.L;
    state.markers.clearLayers();
    state.lines.clearLayers();
    const bounds = [];
    const unique = new Map();
    data.points.forEach((p, index) => {
      const latlng = [p.lat, p.lon];
      bounds.push(latlng);
      const key = latlng.join(',');
      const item = unique.get(key) || {point: p, visits: []};
      item.visits.push(index + 1);
      item.latest = index === data.points.length - 1;
      unique.set(key, item);
    });
    let total = 0;
    let roads = 0;
    data.segments.forEach(segment => {
      const road = segment.state === 'road';
      const points = road ? segment.coordinates : [data.points[segment.from], data.points[segment.to]].map(p => [p.lat, p.lon]);
      bounds.push(...points);
      if (road) {
        roads += 1;
        total += segment.distance_km;
        L.polyline(points, {color: '#fff', weight: 8, opacity: 0.95, interactive: false}).addTo(state.lines);
      }
      L.polyline(points, {color: road ? '#196b61' : '#677c87', weight: road ? 4 : 2.5,
        opacity: road ? 1 : 0.8, dashArray: road ? null : '7 9', interactive: false}).addTo(state.lines);
    });
    unique.forEach(({point: p, visits, latest}) => {
      L.circleMarker([p.lat, p.lon], {radius: latest ? 9 : 6, color: '#fff', weight: 3,
        fillColor: latest ? '#e58c35' : '#196b61', fillOpacity: 1})
        .bindTooltip(text(`${visits.join(' / ')}. ${p.name}${latest ? ' · senest' : ''}`), {
          permanent: true, direction: 'top', offset: [0, -10], className: 'parcel-map-label',
        }).addTo(state.markers);
    });
    if (!state.fitted || (!data.pending && !state.settled)) {
      state.map.fitBounds(bounds, {padding: [45, 55], maxZoom: 10});
      state.fitted = true;
      state.settled = !data.pending;
    }
    status.hidden = false;
    if (data.pending) {
      status.textContent = 'Beregner lastbilruten mellem stederne…';
    } else if (roads) {
      const distance = new Intl.NumberFormat('da-DK', {maximumFractionDigits: 0}).format(total);
      status.textContent = `Ca. ${distance} km ad vej${roads < data.segments.length ? ' · dele af rejsen vises som stiplede forbindelser' : ''}.`;
    } else if (data.points.length === 1) {
      status.textContent = 'Senest kendte sted · ruten vises, når der er flere kendte byer.';
    } else {
      status.textContent = 'Ingen sammenhængende lastbilrute tilgængelig. Stiplede linjer viser rækkefølgen af stederne.';
    }
  }

  async function load(canvas, figure, state) {
    const status = figure.querySelector('[data-map-status]');
    state.loading = true;
    try {
      const response = await fetch(canvas.dataset.mapUrl, {headers: {'Accept': 'application/json'}});
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('map');
      const data = await response.json();
      if (!canvas.isConnected) return;
      state.loaded = true;
      if (!data.points.length) {
        status.textContent = 'Der er endnu ingen genkendelige byer at vise på kortet.';
        return;
      }
      if (!state.map) Object.assign(state, create(canvas, figure));
      draw(state, data, status);
      state.attempts += 1;
      if (data.pending && state.attempts < 25) {
        state.timer = setTimeout(() => {
          state.timer = null;
          if (!canvas.isConnected) return;
          if (canvas.closest('[data-shipment-details]').hidden) { state.loaded = false; return; }
          load(canvas, figure, state);
        }, 1500);
      } else if (data.pending) {
        state.loaded = false;
        status.textContent = 'Ruten beregnes stadig. Åbn kortet igen om lidt.';
      }
    } catch (error) {
      status.hidden = false;
      status.textContent = 'Kortet kunne ikke hentes. Fold pakken sammen og prøv igen.';
      state.loaded = false;
    } finally {
      state.loading = false;
    }
  }

  window.FjordParcelMap = {
    async snapshot(figure, data) {
      const canvas = figure.querySelector('[data-map-url]');
      const state = create(canvas, figure, true);
      draw(state, data, figure.querySelector('[data-map-status]'));
      const started = Date.now();
      let mapError;
      state.basemap.getMaplibreMap()?.on('error', event => { mapError = event.error; });
      await new Promise((resolve, reject) => {
        const check = () => {
          const gl = state.basemap.getMaplibreMap();
          if (mapError) { reject(mapError); return; }
          if (gl?.loaded() && gl.areTilesLoaded()) { resolve(); return; }
          if (Date.now() - started > 18000) { reject(new Error('Map background did not finish loading')); return; }
          setTimeout(check, 100);
        };
        check();
      });
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return {points: data.points, segments: data.segments};
    },
    open(details) {
      const canvas = details.querySelector('[data-map-url]');
      if (!canvas) return;
      let state = instances.get(canvas);
      if (!state) { state = {loading: false, loaded: false, attempts: 0}; instances.set(canvas, state); }
      requestAnimationFrame(() => state.map?.invalidateSize({pan: false}));
      if (!state.loaded && !state.loading && !state.timer) {
        state.attempts = 0;
        load(canvas, canvas.closest('[data-parcel-map]'), state);
      }
    },
  };
})();
