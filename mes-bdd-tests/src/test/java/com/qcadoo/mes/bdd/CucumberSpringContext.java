package com.qcadoo.mes.bdd;

import org.springframework.boot.test.context.SpringBootTest;

import io.cucumber.spring.CucumberContextConfiguration;

/**
 * 把 Cucumber 綁到上面那個 Spring Boot context。
 *
 * <p>用 {@code WebEnvironment.NONE} —— 我們不需要在測試端起一個 web server,
 * 因為受測的 qcadoo 是外部的獨立行程,透過真實 HTTP 溝通。
 */
@CucumberContextConfiguration
@SpringBootTest(
        classes = BddTestConfiguration.class,
        webEnvironment = SpringBootTest.WebEnvironment.NONE)
public class CucumberSpringContext {
}
