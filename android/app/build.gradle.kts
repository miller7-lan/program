import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// 本机专用配置（默认服务器地址、签名信息）只放在 local.properties，不进入公开仓库
// 按 UTF-8 读取：路径里可能有中文（Properties 默认按 ISO-8859-1 解码会出现乱码）
val localProps = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) file.reader(Charsets.UTF_8).use { load(it) }
}

fun localProp(name: String): String = (localProps.getProperty(name) ?: "").trim()

val keystorePropsPath = localProp("profitHelper.keystoreProperties")

android {
    namespace = "com.homeapp.profithelper"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.homeapp.profithelper"
        minSdk = 26
        targetSdk = 36
        versionCode = 113
        versionName = "1.1.3"
        buildConfigField("String", "DEFAULT_SERVER_URL", "\"${localProp("profitHelper.defaultServerUrl")}\"")
        buildConfigField("String", "DEFAULT_FALLBACK_IP", "\"${localProp("profitHelper.defaultFallbackIp")}\"")
        // 内置连接码只放进正式版（见 release）；调试版和测试永远不会自动连上真实账本
        buildConfigField("String", "PRESET_CONNECTION_CODE", "\"\"")
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (keystorePropsPath.isNotEmpty()) {
            create("release") {
                val keystore = Properties().apply { file(keystorePropsPath).reader(Charsets.UTF_8).use { load(it) } }
                storeFile = file(keystore.getProperty("storeFile"))
                storePassword = keystore.getProperty("storePassword")
                keyAlias = keystore.getProperty("keyAlias")
                keyPassword = keystore.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (keystorePropsPath.isNotEmpty()) {
                signingConfig = signingConfigs.getByName("release")
            }
            // 内置连接码：首次打开自动连接服务器，用户无需填写（只来自本机 local.properties）
            buildConfigField("String", "PRESET_CONNECTION_CODE", "\"${localProp("profitHelper.presetConnectionCode")}\"")
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.12.01")
    implementation(composeBom)
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.4")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}
