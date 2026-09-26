<#--
  Каркас всех страниц входа Orbita: вход, ошибки, смена пароля, выход.

  Служебная часть <head> (карта импортов, проверка сессии в соседних вкладках,
  одноразовые ссылки) повторяет base/login/template.ftl Keycloak 26.4 — без неё
  ломаются вход в нескольких вкладках и passkeys. Своё здесь только тело:
  марка, карточка и водяной знак, как на полотне веб-интерфейса.
-->
<#import "footer.ftl" as loginFooter>
<#macro registrationLayout bodyClass="" displayInfo=false displayMessage=true displayRequiredFields=false>
<!DOCTYPE html>
<html class="${properties.kcHtmlClass!}" lang="${lang}"<#if realm.internationalizationEnabled> dir="${(locale.rtl)?then('rtl','ltr')}"</#if>>

<head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="robots" content="noindex, nofollow" />
    <meta name="theme-color" content="#f6f3ed" />
    <title>${msg("loginTitle",(realm.displayName!''))}</title>
    <#-- Знак вкладки — тот же, что у веб-интерфейса: орбита с точкой на ней. -->
    <link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2028%2028%22%20fill%3D%22none%22%3E%3Ccircle%20cx%3D%2214%22%20cy%3D%2214%22%20r%3D%224.6%22%20fill%3D%22%23FF6B2C%22%2F%3E%3Cellipse%20cx%3D%2214%22%20cy%3D%2214%22%20rx%3D%2212.2%22%20ry%3D%226.4%22%20stroke%3D%22%23FF6B2C%22%20stroke-width%3D%221.8%22%20opacity%3D%22.6%22%20transform%3D%22rotate(-28%2014%2014)%22%2F%3E%3Ccircle%20cx%3D%2224%22%20cy%3D%228.4%22%20r%3D%222.1%22%20fill%3D%22%23FF6B2C%22%2F%3E%3C%2Fsvg%3E" />
    <#if properties.stylesCommon?has_content>
        <#list properties.stylesCommon?split(' ') as style>
            <link href="${url.resourcesCommonPath}/${style}" rel="stylesheet" />
        </#list>
    </#if>
    <#if properties.styles?has_content>
        <#list properties.styles?split(' ') as style>
            <link href="${url.resourcesPath}/${style}" rel="stylesheet" />
        </#list>
    </#if>
    <#if properties.scripts?has_content>
        <#list properties.scripts?split(' ') as script>
            <script src="${url.resourcesPath}/${script}" type="text/javascript"></script>
        </#list>
    </#if>
    <script type="importmap">
        {
            "imports": {
                "rfc4648": "${url.resourcesCommonPath}/vendor/rfc4648/rfc4648.js"
            }
        }
    </script>
    <script src="${url.resourcesPath}/js/menu-button-links.js" type="module"></script>
    <#if scripts??>
        <#list scripts as script>
            <script src="${script}" type="text/javascript"></script>
        </#list>
    </#if>
    <script type="module">
        import { startSessionPolling } from "${url.resourcesPath}/js/authChecker.js";

        startSessionPolling(
            "${url.ssoLoginInOtherTabsUrl?no_esc}"
        );
    </script>
    <script type="module">
        document.addEventListener("click", (event) => {
            const link = event.target.closest("a[data-once-link]");

            if (!link) {
                return;
            }

            if (link.getAttribute("aria-disabled") === "true") {
                event.preventDefault();
                return;
            }

            const { disabledClass } = link.dataset;

            if (disabledClass) {
                link.classList.add(...disabledClass.trim().split(/\s+/));
            }

            link.setAttribute("role", "link");
            link.setAttribute("aria-disabled", "true");
        });
    </script>
    <#if authenticationSession??>
        <script type="module">
            import { checkAuthSession } from "${url.resourcesPath}/js/authChecker.js";

            checkAuthSession(
                "${authenticationSession.authSessionIdHash}"
            );
        </script>
    </#if>
</head>

<body class="${properties.kcBodyClass!}" data-page-id="login-${pageId}">
<#-- Водяной знак полотна: на грани видимости, без смысла, только след марки. -->
<svg class="orb-orbit" viewBox="0 0 520 520" aria-hidden="true" focusable="false">
    <g class="orb-ring orb-ring-1">
        <ellipse cx="260" cy="260" rx="240" ry="120" stroke="currentColor" stroke-width="1.5" fill="none" />
        <circle cx="500" cy="260" r="7" fill="currentColor" />
    </g>
    <g class="orb-ring orb-ring-2" transform="rotate(58 260 260)">
        <ellipse cx="260" cy="260" rx="190" ry="95" stroke="currentColor" stroke-width="1.5" fill="none" />
        <circle cx="70" cy="260" r="5" fill="currentColor" />
    </g>
    <g class="orb-ring orb-ring-3" transform="rotate(-34 260 260)">
        <ellipse cx="260" cy="260" rx="135" ry="68" stroke="currentColor" stroke-width="1.5" fill="none" />
    </g>
    <circle cx="260" cy="260" r="34" fill="currentColor" />
</svg>

<main class="${properties.kcLoginClass!}">
    <div id="kc-header" class="orb-brand">
        <svg class="orb-brand-mark" width="26" height="26" viewBox="0 0 28 28" fill="none" aria-hidden="true" focusable="false">
            <circle cx="14" cy="14" r="4.6" fill="currentColor" />
            <ellipse cx="14" cy="14" rx="12.2" ry="6.4" stroke="currentColor" stroke-width="1.6" opacity="0.55" transform="rotate(-28 14 14)" />
            <circle cx="24" cy="8.4" r="2.1" fill="currentColor" opacity="0.85" />
        </svg>
        <span class="orb-brand-name">ORBITA</span>
    </div>

    <section class="${properties.kcFormCardClass!}">
        <header class="${properties.kcFormHeaderClass!}">
            <#if realm.internationalizationEnabled && locale.supported?size gt 1>
                <div class="orb-locale" id="kc-locale">
                    <div id="kc-locale-wrapper">
                        <div id="kc-locale-dropdown" class="menu-button-links orb-locale-dropdown">
                            <button tabindex="1" id="kc-current-locale-link" aria-label="${msg("languages")}" aria-haspopup="true" aria-expanded="false" aria-controls="language-switch1">${locale.current}</button>
                            <ul role="menu" tabindex="-1" aria-labelledby="kc-current-locale-link" aria-activedescendant="" id="language-switch1" class="orb-locale-list">
                                <#assign i = 1>
                                <#list locale.supported as l>
                                    <li role="none">
                                        <a role="menuitem" id="language-${i}" href="${l.url}">${l.label}</a>
                                    </li>
                                    <#assign i++>
                                </#list>
                            </ul>
                        </div>
                    </div>
                </div>
            </#if>
            <#if !(auth?has_content && auth.showUsername() && !auth.showResetCredentials())>
                <h1 id="kc-page-title"><#nested "header"></h1>
                <#-- Подзаголовок нужен только первой странице: на остальных
                     заголовок уже говорит, что происходит. -->
                <#if pageId == "login">
                    <p class="orb-lead">${msg("orbitaLoginLead")}</p>
                </#if>
            <#else>
                <#nested "show-username">
                <div id="kc-username" class="orb-username">
                    <span class="orb-username-label">${msg("orbitaSignedAs")}</span>
                    <span id="kc-attempted-username" class="orb-username-value">${auth.attemptedUsername}</span>
                    <a id="reset-login" class="orb-username-reset" href="${url.loginRestartFlowUrl}" aria-label="${msg("restartLoginTooltip")}" title="${msg("restartLoginTooltip")}">
                        <i class="${properties.kcResetFlowIcon!}" aria-hidden="true"></i>
                    </a>
                </div>
            </#if>
            <#if displayRequiredFields>
                <p class="orb-required"><span class="required">*</span> ${msg("requiredFields")}</p>
            </#if>
        </header>

        <div id="kc-content">
            <div id="kc-content-wrapper">
                <#-- Предупреждения о действии, которое приложение само попросило,
                     во время входа не показываются — так же, как в base. -->
                <#if displayMessage && message?has_content && (message.type != 'warning' || !isAppInitiatedAction??)>
                    <div class="${properties.kcAlertClass!} orb-alert-${message.type}" role="<#if message.type = 'error'>alert<#else>status</#if>">
                        <#if message.type = 'success'><span class="${properties.kcFeedbackSuccessIcon!}" aria-hidden="true"></span></#if>
                        <#if message.type = 'warning'><span class="${properties.kcFeedbackWarningIcon!}" aria-hidden="true"></span></#if>
                        <#if message.type = 'error'><span class="${properties.kcFeedbackErrorIcon!}" aria-hidden="true"></span></#if>
                        <#if message.type = 'info'><span class="${properties.kcFeedbackInfoIcon!}" aria-hidden="true"></span></#if>
                        <span class="${properties.kcAlertTitleClass!}">${kcSanitize(message.summary)?no_esc}</span>
                    </div>
                </#if>

                <#nested "form">

                <#if auth?has_content && auth.showTryAnotherWayLink()>
                    <form id="kc-select-try-another-way-form" action="${url.loginAction}" method="post">
                        <div class="${properties.kcFormGroupClass!}">
                            <input type="hidden" name="tryAnotherWay" value="on"/>
                            <a href="#" id="try-another-way"
                               onclick="document.forms['kc-select-try-another-way-form'].requestSubmit();return false;">${msg("doTryAnotherWay")}</a>
                        </div>
                    </form>
                </#if>

                <#nested "socialProviders">

                <#if displayInfo>
                    <div id="kc-info" class="${properties.kcSignUpClass!}">
                        <div id="kc-info-wrapper" class="${properties.kcInfoAreaWrapperClass!}">
                            <#nested "info">
                        </div>
                    </div>
                </#if>
            </div>
        </div>

        <@loginFooter.content/>
    </section>

    <p class="orb-foot">${msg("orbitaFoot")}</p>
</main>
</body>
</html>
</#macro>
