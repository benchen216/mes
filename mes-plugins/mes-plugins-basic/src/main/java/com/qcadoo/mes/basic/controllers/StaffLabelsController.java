package com.qcadoo.mes.basic.controllers;

import com.qcadoo.mes.basic.constants.BasicConstants;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.servlet.ModelAndView;

import java.util.List;

@Controller
@RequestMapping(value = BasicConstants.PLUGIN_IDENTIFIER, method = RequestMethod.GET)
public class StaffLabelsController {

    @RequestMapping(value = "staffLabelsReport.pdf")
    public final ModelAndView staffLabelsReportPdf(@RequestParam("ids") final List<Long> ids) {
        ModelAndView mav = new ModelAndView();

        mav.setViewName("staffLabelsReportPdf");
        mav.addObject("ids", ids);

        return mav;
    }

    @RequestMapping(value = "productLabelsReport.pdf")
    public final ModelAndView productLabelsReportPdf(@RequestParam("ids") final List<Long> ids) {
        ModelAndView mav = new ModelAndView();

        mav.setViewName("productLabelsReportPdf");
        mav.addObject("ids", ids);

        return mav;
    }

    @RequestMapping(value = "productLabelReport.pdf")
    public final ModelAndView productLabelReportPdf(@RequestParam("number") final String number) {
        ModelAndView mav = new ModelAndView();

        mav.setViewName("productLabelReportPdf");
        mav.addObject("number", number);

        return mav;
    }

}
