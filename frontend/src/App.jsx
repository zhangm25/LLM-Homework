import {
  Brain,
  Clock3,
  Crosshair,
  Loader2,
  MapPin,
  MessageSquareText,
  Search,
  Sparkles,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const AMAP_JS_KEY = import.meta.env.VITE_AMAP_JS_KEY;
const AMAP_SECURITY_SERVICE_HOST = import.meta.env.VITE_AMAP_SECURITY_SERVICE_HOST;
const EXAMPLES = [
  { label: "到北京南站，顺路吃饭", value: "到北京南站，顺路吃饭" },
  { label: "去五棵松看演唱会，顺便吃饭", value: "去五棵松看演唱会，顺便吃饭" },
  { label: "去奥森公园跑步，顺便吃饭", value: "去奥森公园跑步，顺便吃饭" },
];

export default function App() {
  const [query, setQuery] = useState("到北京南站，顺路吃饭");
  const [currentLocation, setCurrentLocation] = useState(null);
  const [locationStatus, setLocationStatus] = useState("尚未获取当前位置。");
  const [planResult, setPlanResult] = useState(null);
  const [taskPlan, setTaskPlan] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [locating, setLocating] = useState(false);
  const [selectedPlanIndex, setSelectedPlanIndex] = useState(0);

  useEffect(() => {
    requestDeviceLocation({
      onStart: () => {
        setLocating(true);
        setLocationStatus("正在请求浏览器定位权限...");
      },
      onSuccess: (location) => {
        setCurrentLocation(location);
        setLocationStatus(
          `已使用当前位置：${location.latitude.toFixed(5)}, ${location.longitude.toFixed(5)}`,
        );
      },
      onError: (message) => {
        setCurrentLocation(null);
        setLocationStatus(message);
      },
      onDone: () => setLocating(false),
    });
  }, []);

  async function handleSubmit(event) {
    event.preventDefault();
    const cleanedQuery = query.trim();
    if (cleanedQuery.length < 2) {
      return;
    }

    setError("");
    setLoading(true);

    try {
      const body = {
        query: cleanedQuery,
        current_location: currentLocation,
      };
      const [taskPlanResponse, travelPlanResponse] = await Promise.all([
        postJson("/api/task-plan", body),
        postJson("/api/plans", body),
      ]);

      setTaskPlan(taskPlanResponse);
      setPlanResult(travelPlanResponse);
      setSelectedPlanIndex(0);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleLocate() {
    setError("");
    requestDeviceLocation({
      onStart: () => {
        setLocating(true);
        setLocationStatus("正在请求浏览器定位权限...");
      },
      onSuccess: (location) => {
        setCurrentLocation(location);
        setLocationStatus(
          `已使用当前位置：${location.latitude.toFixed(5)}, ${location.longitude.toFixed(5)}`,
        );
      },
      onError: (message) => {
        setCurrentLocation(null);
        setLocationStatus(message);
      },
      onDone: () => setLocating(false),
    });
  }

  const selectedPlan = planResult?.plans?.[selectedPlanIndex] ?? planResult?.plans?.[0];
  const displayedQuery = taskPlan?.original_query ?? query;

  return (
    <main className="app-shell">
      <section className="workspace">
        <aside className="query-panel">
          <div>
            <p className="eyebrow">任务导向路线助手</p>
            <h1>智能路线规划 Demo</h1>
            <p className="intro">
              输入自然语言需求，系统会拆解任务、搜索候选地点、规划路线，并解释推荐理由。
            </p>
          </div>

          <form onSubmit={handleSubmit} className="query-form">
            <label htmlFor="query">你的需求</label>
            <textarea
              id="query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              rows={5}
              placeholder="例如：到北京南站，顺路吃饭；去五棵松看演唱会，顺便吃饭；去奥森公园跑步，顺便吃饭"
            />

            <div className="location-box">
              <button className="secondary-button" type="button" onClick={handleLocate} disabled={locating}>
                {locating ? <Loader2 className="spin" size={18} /> : <Crosshair size={18} />}
                使用当前位置
              </button>
              <p>{locationStatus}</p>
            </div>

            <button type="submit" disabled={loading || query.trim().length < 2}>
              {loading ? <Loader2 className="spin" size={18} /> : <Search size={18} />}
              生成路线
            </button>
          </form>

          <div className="example-strip" aria-label="示例需求">
            {EXAMPLES.map((sample) => (
              <button
                className="sample-button"
                type="button"
                key={sample.value}
                onClick={() => setQuery(sample.value)}
              >
                {sample.label}
              </button>
            ))}
          </div>

          {error && <p className="error">{error}</p>}
        </aside>

        <section className="result-panel">
          {!selectedPlan && !taskPlan && (
            <div className="empty-state">
              <Sparkles size={32} />
              <p>提交需求后，这里会展示任务拆解、候选地点和推荐路线。</p>
            </div>
          )}

          {(selectedPlan || taskPlan) && (
            <>
              <section className="overview-band">
                <div>
                  <p className="eyebrow">当前需求</p>
                  <h2>{displayedQuery}</h2>
                </div>
                {planResult?.intent && (
                  <div className="intent-bar">
                    <span>{translateDisplayText(planResult.intent.destination)}</span>
                    <span>{translateDisplayText(planResult.intent.duration)}</span>
                    <span>{planResult.intent.preferences.map(translateDisplayText).join(" / ")}</span>
                    {currentLocation && <span>已启用当前位置</span>}
                  </div>
                )}
              </section>

              <section className="pipeline-grid">
                <Panel title="系统理解" icon={<Brain size={18} />}>
                  <ol className="task-chain">
                    {taskPlan?.tasks.map((task) => (
                      <li key={`${task.order}-${task.action}`}>
                        <span>{task.order}</span>
                        <div>
                          <strong>{translateDisplayText(task.action)}</strong>
                          <p>
                            {translateDisplayText(task.category)} · {translateDisplayText(task.required_resource)}
                          </p>
                        </div>
                      </li>
                    ))}
                  </ol>
                </Panel>

                <Panel title="候选地点" icon={<MapPin size={18} />}>
                  <div className="candidate-list">
                    {taskPlan?.tasks.map((task) => (
                      <div className="candidate-group" key={`${task.order}-${task.category}`}>
                        <div className="candidate-heading">
                          <strong>{translateDisplayText(task.action)}</strong>
                          <span>{translateDisplayText(task.query_hint)}</span>
                        </div>
                        <div className="tag-row">
                          {task.candidate_poi_types.map((poiType) => (
                            <span title={translateDisplayText(poiType.reason)} key={`${task.order}-${poiType.type}`}>
                              {translateDisplayText(poiType.type)}
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>

                  {selectedPlan?.pois?.length > 0 && (
                    <div className="poi-results">
                      {selectedPlan.pois.map((poi) => (
                        <div className="poi-item" key={poi.name}>
                          <strong>{translateDisplayText(poi.name)}</strong>
                          <span>
                            {translateDisplayText(poi.category)} · 停留 {poi.stay_minutes} 分钟
                          </span>
                          {poi.address && <small>{poi.address}</small>}
                          <p>{translateDisplayText(poi.reason)}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </Panel>
              </section>

              {selectedPlan && (
                <section className="plan-card">
                  {planResult?.plans?.length > 1 && (
                    <div className="route-options">
                      {planResult.plans.map((plan, index) => (
                        <button
                          className={index === selectedPlanIndex ? "route-option active" : "route-option"}
                          type="button"
                          key={plan.title}
                          onClick={() => setSelectedPlanIndex(index)}
                        >
                          <span>{translateDisplayText(plan.title)}</span>
                          <strong>{Math.round(plan.score * 100)} 分</strong>
                        </button>
                      ))}
                    </div>
                  )}

                  <div className="plan-header">
                    <div>
                      <p className="eyebrow">推荐方案</p>
                      <h2>{translateDisplayText(selectedPlan.title)}</h2>
                    </div>
                    <strong>{Math.round(selectedPlan.score * 100)} 分</strong>
                  </div>

                  <p className="summary">{translateDisplayText(selectedPlan.summary)}</p>

                  <Panel title="路线时间线" icon={<Clock3 size={18} />} compact>
                    <ol className="timeline">
                      {selectedPlan.route.map((leg, index) => (
                        <li key={`${leg.from}-${leg.to}-${index}`}>
                          <div className="timeline-marker">{index + 1}</div>
                          <div className="timeline-content">
                            <strong>
                              {translateDisplayText(leg.from)} -&gt; {translateDisplayText(leg.to)}
                            </strong>
                            <span>
                              {translateDisplayText(leg.transport)} · 约 {leg.duration_minutes} 分钟
                            </span>
                            {selectedPlan.pois[index] && (
                              <p>
                                在 {translateDisplayText(selectedPlan.pois[index].name)} 停留{" "}
                                {selectedPlan.pois[index].stay_minutes} 分钟。
                              </p>
                            )}
                            {leg.steps?.length > 0 && (
                              <ul className="leg-steps">
                                {leg.steps.map((step, stepIndex) => (
                                  <li key={`${leg.to}-${stepIndex}`}>{translateDisplayText(step)}</li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </li>
                      ))}
                    </ol>
                  </Panel>

                  <Panel title="地图路线" icon={<MapPin size={18} />} compact>
                    <RouteMap plan={selectedPlan} currentLocation={currentLocation} />
                  </Panel>

                  <Panel title="推荐理由" icon={<MessageSquareText size={18} />} compact>
                    <p className="explanation">{translateDisplayText(selectedPlan.explanation)}</p>
                  </Panel>
                </section>
              )}
            </>
          )}
        </section>
      </section>
    </main>
  );
}

function RouteMap({ plan, currentLocation }) {
  const mapRef = useRef(null);
  const [mapStatus, setMapStatus] = useState("");

  useEffect(() => {
    if (!AMAP_JS_KEY || !mapRef.current || !plan) {
      return;
    }

    let cancelled = false;
    loadAMap()
      .then((AMap) => {
        if (cancelled || !mapRef.current) {
          return;
        }
        mapRef.current.innerHTML = "";
        const map = new AMap.Map(mapRef.current, {
          zoom: 14,
          viewMode: "2D",
        });

        const points = routePoints(plan, currentLocation);
        if (points.length > 0) {
          map.setCenter(points[0]);
        }

        points.forEach((point, index) => {
          new AMap.Marker({
            map,
            position: point,
            title: index === 0 ? "起点" : translateDisplayText(plan.pois[index - 1]?.name),
            label: {
              content: index === 0 ? "起点" : `${index}`,
              direction: "top",
            },
          });
        });

        const polylinePoints = routePolylinePoints(plan);
        const linePath = polylinePoints.length > 0 ? polylinePoints : points;
        if (linePath.length > 1) {
          new AMap.Polyline({
            map,
            path: linePath,
            strokeColor: "#244b7a",
            strokeWeight: 6,
            strokeOpacity: 0.86,
          });
          map.setFitView();
        }
        setMapStatus("");
      })
      .catch(() => {
        setMapStatus("高德地图加载失败，已显示内置路线示意图。");
      });

    return () => {
      cancelled = true;
    };
  }, [plan, currentLocation]);

  if (!AMAP_JS_KEY || mapStatus) {
    return (
      <div className="map-fallback">
        <div className="map-path">
          <span>起点</span>
          {plan.pois.map((poi, index) => (
            <span key={poi.name}>{index + 1}</span>
          ))}
        </div>
        <div className="map-place-list">
          {plan.pois.map((poi, index) => (
            <p key={poi.name}>
              {index + 1}. {translateDisplayText(poi.name)}
            </p>
          ))}
        </div>
        <small>
          {mapStatus || "在 frontend/.env 中设置 VITE_AMAP_JS_KEY 后可显示实时高德地图。"}
        </small>
      </div>
    );
  }

  return <div className="amap-container" ref={mapRef} />;
}

function Panel({ title, icon, compact = false, children }) {
  return (
    <section className={compact ? "info-panel compact" : "info-panel"}>
      <h3>
        {icon}
        {title}
      </h3>
      {children}
    </section>
  );
}

function translateDisplayText(value) {
  if (value === null || value === undefined) {
    return "";
  }

  let text = String(value);
  const exactTranslations = {
    eat: "吃饭",
    food: "餐饮",
    cafe: "咖啡",
    study: "学习",
    sports: "运动",
    errand: "事务",
    return: "返回",
    general: "通用任务",
    poi_search: "地点搜索",
    route_planning: "路线规划",
    task_planning: "任务规划",
    restaurant: "餐厅",
    snack: "小吃",
    canteen: "食堂",
    sports_court: "运动场地",
    gym: "健身房",
    campus_playground: "校园操场",
    coffee_shop: "咖啡店",
    bakery: "面包甜品店",
    library: "图书馆",
    study_room: "自习空间",
    bookstore: "书店",
    package_station: "快递驿站",
    delivery_locker: "智能快递柜",
    service_counter: "服务台",
    user_destination: "用户目的地",
    transit_stop: "公交/地铁站",
    Destination: "目的地",
    "Railway station": "火车站",
    Food: "餐饮",
    Cafe: "咖啡",
    Study: "学习",
    Sports: "运动",
    Errand: "事务",
    Return: "返回",
    General: "通用地点",
    "Current location": "当前位置",
    "Current area": "当前区域",
    "destination route": "目的地路线",
    "multi-stop route": "多点路线",
    "half day to one day": "半天到一天",
    "one day": "一天",
    "destination routing": "目的地路线",
    "on-the-way stop": "顺路停靠",
    "nearby POIs": "附近地点",
    "shorter route": "路线更短",
    "classic sights": "经典景点",
    "smooth routing": "路线顺畅",
    "nature views": "自然风景",
    "low effort": "轻松省力",
    "AMap taxi/driving": "高德驾车/打车",
    "Metro or bus + short walk": "地铁或公交 + 短距离步行",
    "Walk, bike, or short taxi": "步行、骑行或短途打车",
    "Bus, bike, or taxi": "公交、骑行或打车",
    Walk: "步行",
    Taxi: "打车",
  };

  if (exactTranslations[text]) {
    return exactTranslations[text];
  }

  const replacements = [
    [/^AMap Taxi Route to (.+)$/i, "高德打车路线：前往 $1"],
    [/^Transit-first Route to (.+)$/i, "公交/地铁优先路线：前往 $1"],
    [/^Alternative Dining Route to (.+)$/i, "备选吃饭路线：前往 $1"],
    [/^On-the-way Dining Stop before (.+)$/i, "前往 $1 途中的吃饭点"],
    [/^Nearby Dining Stop$/i, "附近吃饭点"],
    [/^Nearby Coffee Stop$/i, "附近咖啡点"],
    [/^Nearby Study Space$/i, "附近学习空间"],
    [/^Nearby Sports Court$/i, "附近运动场地"],
    [/^Nearby Package Pickup$/i, "附近取件点"],
    [/^Return Destination$/i, "返回目的地"],
    [/^Current Location Compact Route$/i, "当前位置紧凑路线"],
    [/^Current Location Alternative Route$/i, "当前位置备选路线"],
    [/^AMap Route Option (\d+)$/i, "高德路线方案 $1"],
  ];

  for (const [pattern, replacement] of replacements) {
    if (pattern.test(text)) {
      return text.replace(pattern, replacement);
    }
  }

  const phraseTranslations = [
    ["restaurant or dining place", "餐厅或吃饭地点"],
    ["coffee shop", "咖啡店"],
    ["library or study space", "图书馆或自习空间"],
    ["sports court or gym", "运动场地或健身房"],
    ["package pickup point", "快递取件点"],
    ["destination routing", "目的地路线规划"],
    ["Selected from AMap nearby POI search for the parsed task chain.", "根据任务链从高德附近地点搜索中选出。"],
    ["Final destination resolved by AMap geocoding.", "最终目的地由高德地理编码解析得到。"],
    [
      "Selected from AMap POI search near the path from your current location to the destination.",
      "从当前位置到目的地的路径附近通过高德地点搜索选出。",
    ],
    [
      "This plan keeps the requested destination as the final stop and inserts the dining POI before it.",
      "该方案将你指定的目的地作为最后一站，并在抵达前插入吃饭地点。",
    ],
    [
      "Taxi options use AMap driving route data; transit-first options are labeled as public-transport suggestions with distance-based estimates until a dedicated transit API is added.",
      "打车方案使用高德驾车路线数据；公交/地铁优先方案在接入专门公交接口前，先按距离给出估算建议。",
    ],
    [
      "This fallback route keeps the requested destination as the final stop.",
      "该兜底路线会把你指定的目的地保留为最后一站。",
    ],
    [
      "Placed between your current location and",
      "已放置在当前位置和",
    ],
    ["Matches the eating task in your request.", "匹配你需求中的吃饭任务。"],
    ["Matches the coffee task in your request.", "匹配你需求中的咖啡任务。"],
    ["Matches the study or library task in your request.", "匹配你需求中的学习或图书馆任务。"],
    ["Matches the sports task in your request.", "匹配你需求中的运动任务。"],
    ["Matches the package pickup task in your request.", "匹配你需求中的取件任务。"],
    ["Real AMap-backed route", "真实高德路线"],
    ["from your current location", "从当前位置出发"],
    ["with an on-the-way dining stop", "并包含顺路吃饭点"],
    ["Taxi-style route", "打车路线"],
    ["Transit-oriented route", "公交/地铁优先路线"],
    ["Generated from AMap POI search and walking route planning.", "由高德地点搜索和步行路线规划生成。"],
  ];

  for (const [source, target] of phraseTranslations) {
    text = text.replaceAll(source, target);
  }

  return text;
}

function requestDeviceLocation({ onStart, onSuccess, onError, onDone }) {
  onStart();
  getDeviceLocation()
    .then(onSuccess)
    .catch((err) => onError(err.message))
    .finally(onDone);
}

function getDeviceLocation() {
  if (!navigator.geolocation) {
    return Promise.reject(new Error("当前浏览器不支持定位。"));
  }

  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        resolve({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy_meters: position.coords.accuracy,
          label: "当前位置",
        });
      },
      (error) => {
        reject(new Error(locationErrorMessage(error)));
      },
      {
        enableHighAccuracy: true,
        timeout: 10000,
        maximumAge: 60000,
      },
    );
  });
}

function loadAMap() {
  if (window.AMap) {
    return Promise.resolve(window.AMap);
  }

  if (AMAP_SECURITY_SERVICE_HOST) {
    window._AMapSecurityConfig = { serviceHost: AMAP_SECURITY_SERVICE_HOST };
  }

  return new Promise((resolve, reject) => {
    const existing = document.querySelector("script[data-amap-js]");
    if (existing) {
      existing.addEventListener("load", () => resolve(window.AMap));
      existing.addEventListener("error", reject);
      return;
    }

    const script = document.createElement("script");
    script.dataset.amapJs = "true";
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${AMAP_JS_KEY}`;
    script.async = true;
    script.onload = () => resolve(window.AMap);
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

function routePoints(plan, currentLocation) {
  const points = [];
  if (currentLocation) {
    points.push([currentLocation.longitude, currentLocation.latitude]);
  }
  plan.pois.forEach((poi) => {
    if (typeof poi.longitude === "number" && typeof poi.latitude === "number") {
      points.push([poi.longitude, poi.latitude]);
    }
  });
  return points;
}

function routePolylinePoints(plan) {
  return plan.route
    .flatMap((leg) => (leg.polyline || "").split(";"))
    .map((pair) => pair.split(",").map(Number))
    .filter((point) => point.length === 2 && point.every(Number.isFinite));
}

function locationErrorMessage(error) {
  if (error.code === error.PERMISSION_DENIED) {
    return "定位权限被拒绝，将使用通用起点生成路线。";
  }
  if (error.code === error.POSITION_UNAVAILABLE) {
    return "当前位置不可用，将使用通用起点生成路线。";
  }
  if (error.code === error.TIMEOUT) {
    return "定位请求超时，将使用通用起点生成路线。";
  }
  return "无法读取当前位置，将使用通用起点生成路线。";
}

async function postJson(path, body) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new Error("路线规划失败，请检查后端服务是否正在运行。");
  }

  return response.json();
}
