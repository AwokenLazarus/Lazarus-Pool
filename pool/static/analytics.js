// Google Analytics 4 and PostHog for lazarus-xbt.xyz and pool.lazarus-xbt.xyz.
//
// One GA4 property ("Lazarus Pool websites", stream G-CFKT50ZPQE) and one PostHog project
// ("Lazarus sites", 625142) cover both hosts. Both set their cookies on .lazarus-xbt.xyz, so a visit
// that goes from the hub to the pool stays one visitor and one session, and reports split by host.
// The hub serves this file as /assets/analytics.js and the pool as /static/analytics.js; the two
// copies must stay identical (cloudflare/pool-site/tests/indexing.py checks).
(function () {
  // Only the production hosts report: *.pages.dev previews, local servers and the node stay out.
  var host = location.hostname;
  if (host !== "lazarus-xbt.xyz" && host !== "pool.lazarus-xbt.xyz") return;

  var GA4 = "G-CFKT50ZPQE";
  var tag = document.createElement("script");
  tag.async = true;
  tag.src = "https://www.googletagmanager.com/gtag/js?id=" + GA4;
  document.head.appendChild(tag);
  window.dataLayer = window.dataLayer || [];
  window.gtag = function () { window.dataLayer.push(arguments); };
  window.gtag("js", new Date());
  window.gtag("config", GA4);

  // PostHog's loader as published at posthog.com/docs/libraries/js: it queues calls until
  // array.js arrives from us-assets.i.posthog.com.
  !function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],Object.defineProperty(u,"toString",{configurable:!0,enumerable:!0,writable:!0,value:function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e}}),Object.defineProperty(u.people,"toString",{configurable:!0,enumerable:!0,writable:!0,value:function(){return u.toString(1)+".people (stub)"}}),o="init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagResult isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);
  // The project token is write-only and meant to ship in public pages.
  window.posthog.init("phc_r27v8tfk5zd9AVat9hEHu2Ev7RMynSYZxnbXxf5Znewb", {
    api_host: "https://us.i.posthog.com",
    defaults: "2026-05-30",
  });
})();
